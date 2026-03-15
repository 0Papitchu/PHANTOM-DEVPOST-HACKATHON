# phantom-ui-navigator/agents/screenshot_agent.py
"""
Screenshot Agent — Capture l'écran du navigateur Playwright.
Upload vers GCS + publie un event Pub/Sub.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from google.cloud import storage, pubsub_v1
from playwright.async_api import async_playwright, Page, Browser

from config.settings import settings

logger = logging.getLogger("phantom.screenshot")


class ScreenshotAgent:
    """
    Agent 1/3 — Capture d'écran périodique.
    
    Workflow :
    1. Lance un navigateur Playwright
    2. Navigue vers l'URL cible
    3. Capture un screenshot toutes les X secondes
    4. Upload le screenshot sur Cloud Storage
    5. Publie un event sur Pub/Sub (phantom-screenshots)
    """

    def __init__(self):
        self.browser: Optional[Browser] = None
        self.page: Optional[Page] = None
        self._storage_client = None
        self._publisher = None
        self._topic_path = None
        self._bucket = None
        self._running = False
        self._session_id: Optional[str] = None

    @property
    def publisher(self):
        if self._publisher is None:
            try:
                self._publisher = pubsub_v1.PublisherClient()
                self._topic_path = self._publisher.topic_path(
                    settings.gcp_project_id, settings.pubsub_topic_screenshots
                )
            except Exception as e:
                logger.warning(f"⚠️ Pub/Sub non disponible : {e}")
        return self._publisher

    @property
    def bucket(self):
        if self._bucket is None:
            try:
                self._storage_client = storage.Client()
                self._bucket = self._storage_client.bucket(settings.storage_bucket)
            except Exception as e:
                logger.warning(f"⚠️ Storage non disponible : {e}")
        return self._bucket

    async def start_browser(self, url: Optional[str] = None) -> str:
        """Lance le navigateur et navigue vers l'URL cible."""
        target_url = url or settings.browser_default_url
        self._session_id = str(uuid.uuid4())[:8]

        logger.info(f"🚀 Lancement du navigateur — session {self._session_id}")
        logger.info(f"🌐 URL cible : {target_url}")

        pw = await async_playwright().start()
        # Expose Chrome DevTools Protocol for the MCP server via remote debugging.
        # This keeps our primary interaction model coordinate-based while allowing
        # the DevTools MCP to introspect DOM/console/network when explicitly invoked.
        self.browser = await pw.chromium.launch(
            headless=settings.browser_headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--remote-debugging-port=9222",
            ],
        )
        # Stealth context — realistic browser fingerprint to avoid bot detection
        context = await self.browser.new_context(
            viewport={
                "width": settings.browser_viewport_width,
                "height": settings.browser_viewport_height,
            },
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
            },
        )
        # Prevent navigator.webdriver detection
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        self.page = await context.new_page()
        await self.page.goto(target_url, wait_until="domcontentloaded", timeout=30000)

        logger.info(f"✅ Navigateur prêt — page chargée")
        return self._session_id


    async def take_screenshot(self) -> dict:
        """
        Capture un screenshot, upload sur GCS (si dispo), publie sur Pub/Sub (si dispo).
        Retourne les metadata du screenshot.
        """
        if not self.page:
            raise RuntimeError("Browser non lancé. Appeler start_browser() d'abord.")

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        filename = f"screenshots/{self._session_id}/{timestamp}.{settings.screenshot_format}"

        # 1. Capture screenshot en bytes
        screenshot_bytes = await self.page.screenshot(
            type=settings.screenshot_format,
            full_page=False,
        )
        logger.info(f"📸 Screenshot capturé — {len(screenshot_bytes)} bytes")

        gcs_uri = f"gs://{settings.storage_bucket}/{filename}"

        # 2. Upload vers Cloud Storage (skip si non disponible)
        if self.bucket:
            try:
                blob = self.bucket.blob(filename)
                blob.upload_from_string(
                    screenshot_bytes,
                    content_type=f"image/{settings.screenshot_format}",
                )
                logger.info(f"☁️ Upload GCS — {gcs_uri}")
            except Exception as e:
                logger.warning(f"⚠️ Upload GCS échoué : {e}")

        # 3. Metadata du screenshot
        metadata = {
            "session_id": self._session_id,
            "timestamp": timestamp,
            "gcs_uri": gcs_uri,
            "filename": filename,
            "page_url": self.page.url,
            "page_title": await self.page.title(),
            "viewport": f"{settings.browser_viewport_width}x{settings.browser_viewport_height}",
        }

        # 4. Publie event sur Pub/Sub (skip si non disponible)
        if self.publisher:
            try:
                import json
                message_data = json.dumps(metadata).encode("utf-8")
                future = self.publisher.publish(self._topic_path, data=message_data)
                message_id = future.result(timeout=10)
                logger.info(f"📡 Pub/Sub event publié — message_id={message_id}")
            except Exception as e:
                logger.warning(f"⚠️ Pub/Sub publish échoué : {e}")

        return metadata

    async def start_capture_loop(self, interval: Optional[float] = None):
        """Boucle de capture périodique."""
        capture_interval = interval or settings.screenshot_interval_seconds
        self._running = True

        logger.info(f"🔄 Boucle de capture démarrée — intervalle {capture_interval}s")

        while self._running:
            try:
                metadata = await self.take_screenshot()
                logger.debug(f"Capture OK — {metadata['filename']}")
            except Exception as e:
                logger.error(f"❌ Erreur capture : {e}")
            
            await asyncio.sleep(capture_interval)

    def stop_capture_loop(self):
        """Arrête la boucle de capture."""
        self._running = False
        logger.info("⏹️ Boucle de capture arrêtée")

    async def navigate(self, url: str):
        """Navigue vers une nouvelle URL."""
        if not self.page:
            raise RuntimeError("Browser non lancé.")
        logger.info(f"🌐 Navigation vers : {url}")
        await self.page.goto(url, wait_until="domcontentloaded")

    async def get_current_state(self) -> dict:
        """Retourne l'état actuel du navigateur."""
        if not self.page:
            return {"status": "browser_not_started"}
        return {
            "session_id": self._session_id,
            "url": self.page.url,
            "title": await self.page.title(),
            "running": self._running,
        }

    async def close(self):
        """Ferme le navigateur proprement."""
        self._running = False
        if self.browser:
            await self.browser.close()
            self.browser = None
            self.page = None
            logger.info("🔒 Navigateur fermé")

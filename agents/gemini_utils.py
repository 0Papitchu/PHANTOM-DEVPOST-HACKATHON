# phantom-ui-navigator/agents/gemini_utils.py
"""
Gemini API Utilities — Retry with Exponential Backoff.
Shared by all agents for robust Gemini API calls.

@module gemini_utils
@description Provides retry-wrapped Gemini API calls with exponential backoff
@author ANTIGRAVITY
@created 2026-03-06
@dependencies google-genai
@used-by agents/analyzer_agent.py, agents/action_agent.py
"""

import asyncio
import logging
import os
import time
from typing import Optional

from google import genai
from google.genai import types

from config.settings import settings

logger = logging.getLogger("phantom.gemini")

# ── Constants ────────────────────────────────────────────────
MAX_RETRIES = 3
BASE_DELAY_SECONDS = 1.0
BACKOFF_MULTIPLIER = 2.0
RETRYABLE_STATUS_CODES = {429, 500, 503}


# ── Singleton Client ─────────────────────────────────────────

_gemini_client: Optional[genai.Client] = None


def get_gemini_client() -> Optional[genai.Client]:
    """
    Returns a singleton Gemini client.
    Priority order:
      1. Vertex AI (uses GCP credits — $300 free tier)
      2. API key from .env (AI Studio free tier — very low quotas)
      3. API key from Secret Manager (production)
    """
    global _gemini_client
    if _gemini_client is not None:
        return _gemini_client

    # ── Try 1: Vertex AI (uses GCP $300 free credits) ────────
    # Works with SA key file OR gcloud Application Default Credentials
    creds_path = settings.google_application_credentials
    if creds_path and os.path.exists(creds_path):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = os.path.abspath(creds_path)

    try:
        _gemini_client = genai.Client(
            vertexai=True,
            project=settings.gcp_project_id,
            location=settings.gcp_region,
        )
        logger.info(
            f"🧠 Gemini client (Vertex AI) initialisé — "
            f"projet {settings.gcp_project_id}, modèle {settings.gemini_model}"
        )
        logger.info("💰 Utilise les crédits GCP ($300 free tier) — pas de limite AI Studio")
        return _gemini_client
    except Exception as e:
        logger.warning(f"⚠️ Vertex AI non disponible : {e}")
        logger.info("🔄 Fallback vers API key AI Studio...")

    # ── Try 2: API key from .env (AI Studio) ─────────────────
    api_key = settings.gemini_api_key
    if not api_key:
        try:
            from google.cloud import secretmanager
            sm_client = secretmanager.SecretManagerServiceClient()
            response = sm_client.access_secret_version(
                name=settings.gemini_secret_name
            )
            api_key = response.payload.data.decode("utf-8")
        except Exception as e:
            logger.warning(f"⚠️ Secret Manager non disponible : {e}")
            logger.warning("⚠️ Set GEMINI_API_KEY in .env for local dev")
            return None

    _gemini_client = genai.Client(api_key=api_key)
    logger.info(f"🧠 Gemini client (API key) initialisé — modèle {settings.gemini_model}")
    logger.warning("⚠️ Mode AI Studio — quotas limités. Utilisez Vertex AI pour les crédits GCP.")
    return _gemini_client


# ── Retry Wrapper ────────────────────────────────────────────

async def gemini_generate_with_retry(
    contents: list,
    model: Optional[str] = None,
    config: Optional[types.GenerateContentConfig] = None,
) -> Optional[object]:
    """
    Calls Gemini generate_content with retry and exponential backoff.

    @param contents List of content parts (text, images, etc.)
    @param model Gemini model name (defaults to settings.gemini_model)
    @param config GenerateContentConfig for the request
    @returns Gemini response object or None if all retries fail
    """
    client = get_gemini_client()
    if not client:
        logger.error("❌ Gemini client not available — cannot generate content")
        return None

    target_model = model or settings.gemini_model
    delay = BASE_DELAY_SECONDS

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = await asyncio.to_thread(
                client.models.generate_content,
                model=target_model,
                contents=contents,
                config=config,
            )
            if attempt > 1:
                logger.info(f"✅ Gemini call succeeded on attempt {attempt}")
            return response

        except Exception as e:
            error_str = str(e)
            is_retryable = any(
                str(code) in error_str
                for code in RETRYABLE_STATUS_CODES
            ) or "deadline" in error_str.lower() or "timeout" in error_str.lower()

            if is_retryable and attempt < MAX_RETRIES:
                logger.warning(
                    f"⚠️ Gemini API error (attempt {attempt}/{MAX_RETRIES}): {e}"
                    f" — retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)
                delay *= BACKOFF_MULTIPLIER
            else:
                logger.error(
                    f"❌ Gemini API error (attempt {attempt}/{MAX_RETRIES}): {e}"
                    f" — {'no more retries' if attempt >= MAX_RETRIES else 'non-retryable'}"
                )
                raise

    return None

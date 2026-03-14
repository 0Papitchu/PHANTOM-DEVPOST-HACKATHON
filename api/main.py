# phantom-ui-navigator/api/main.py
"""
Phantom UI Navigator — FastAPI Backend
WebSocket bidirectionnel pour la voix + commandes en temps réel.
REST endpoints pour le contrôle et le monitoring.
"""

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from google.cloud import texttospeech
import base64

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from config.settings import settings
from agents.screenshot_agent import ScreenshotAgent
from agents.analyzer_agent import AnalyzerAgent, UIState
from agents.action_agent import ActionAgent, ActionPlan

# ── Logging ──────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s │ %(name)-20s │ %(levelname)-7s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phantom.api")


# ── State global ─────────────────────────────────────────────

class PhantomState:
    """État global de l'application Phantom."""
    def __init__(self):
        self.screenshot_agent: Optional[ScreenshotAgent] = None
        self.analyzer_agent: Optional[AnalyzerAgent] = None
        self.action_agent: Optional[ActionAgent] = None
        self.current_ui_state: Optional[UIState] = None
        self.session_id: Optional[str] = None
        self.is_running: bool = False
        self.accessibility_mode: bool = False  # Navigator for All pivot
        self.adk_agents: Optional[dict] = None  # ADK framework agents
        self.connected_clients: list[WebSocket] = []

phantom = PhantomState()

# ── GCP TTS Client ───────────────────────────────────────────
try:
    tts_client = texttospeech.TextToSpeechClient()
    logger.info("🎙️ Google Cloud TTS Client initialisé")
except Exception as e:
    logger.warning(f"⚠️ Impossible d'initialiser TTS : {e}")
    tts_client = None

async def generate_tts_audio(text: str) -> Optional[str]:
    """Génère l'audio TTS via GCP de manière asynchrone pour ne pas bloquer."""
    if not tts_client:
        return None
    try:
        def _tts():
            synthesis_input = texttospeech.SynthesisInput(text=text)
            voice = texttospeech.VoiceSelectionParams(
                language_code="en-US",
                name="en-US-Neural2-D",  # Voix grave premium (Phantom persona)
            )
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.MP3
            )
            response = tts_client.synthesize_speech(
                input=synthesis_input, voice=voice, audio_config=audio_config
            )
            return base64.b64encode(response.audio_content).decode("utf-8")
        
        return await asyncio.to_thread(_tts)
    except Exception as e:
        logger.error(f"❌ Erreur TTS : {e}")
        return None


# ── Lifespan ─────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup & shutdown de l'app."""
    logger.info("🛸 PHANTOM UI Navigator — Démarrage...")
    logger.info(f"📋 GCP Project : {settings.gcp_project_id}")
    logger.info(f"🧠 Modèle Gemini : {settings.gemini_model}")
    logger.info(f"☁️ Storage Bucket : {settings.storage_bucket}")

    # Initialize ADK agents (framework backbone)
    try:
        from agents.adk_agents import get_adk_agents
        adk_agents = get_adk_agents()
        phantom.adk_agents = adk_agents
        logger.info("✅ ADK Framework initialisé")
    except Exception as e:
        logger.warning(f"⚠️ ADK init skipped: {e}")
        phantom.adk_agents = None

    # Initialiser l'Analyzer Agent (toujours prêt)
    phantom.analyzer_agent = AnalyzerAgent()
    logger.info("✅ Analyzer Agent initialisé")

    yield

    # Shutdown propre
    if phantom.screenshot_agent:
        await phantom.screenshot_agent.close()
    logger.info("🔒 PHANTOM arrêté proprement")


# ── FastAPI App ──────────────────────────────────────────────

app = FastAPI(
    title="Phantom UI Navigator",
    description="Agent IA qui navigue n'importe quelle UI par vision + voix",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Servir le frontend
app.mount("/static", StaticFiles(directory="frontend"), name="static")


# ── Modèles Pydantic ─────────────────────────────────────────

class StartSessionRequest(BaseModel):
    url: str = settings.browser_default_url
    headless: bool = True
    accessibility_mode: bool = False


class CommandRequest(BaseModel):
    intent: str  # Ce que l'utilisateur veut faire
    auto_execute: bool = True  # Exécuter immédiatement ou juste planifier


class ActionRequest(BaseModel):
    action_type: str  # click, type, scroll, etc.
    target: str       # description de l'élément cible
    value: Optional[str] = None


# ── REST Endpoints ───────────────────────────────────────────

@app.get("/")
async def root():
    """Sert la page d'accueil du frontend."""
    return FileResponse("frontend/index.html")


@app.get("/api/health")
async def health():
    """Health check."""
    return {
        "status": "ok",
        "service": "phantom-ui-navigator",
        "session_active": phantom.is_running,
        "session_id": phantom.session_id,
    }


@app.post("/api/session/start")
async def start_session(req: StartSessionRequest):
    """
    Démarre une session Phantom :
    1. Lance le navigateur Playwright
    2. Navigue vers l'URL cible
    3. Prend un premier screenshot + analyse
    """
    if phantom.is_running:
        logger.warning("⚠️ Session déjà active (probablement suite à un crash/reload). Arrêt forcé...")
        await stop_session()

    logger.info(f"🚀 Nouvelle session — URL: {req.url}")
    
    # On marque la session active immédiatement
    phantom.is_running = True
    phantom.accessibility_mode = req.accessibility_mode

    try:
        # Démarrer le Screenshot Agent
        phantom.screenshot_agent = ScreenshotAgent()
        session_id = await phantom.screenshot_agent.start_browser(req.url)
        phantom.session_id = session_id

        # Premier screenshot + analyse
        metadata = await phantom.screenshot_agent.take_screenshot()
        screenshot_bytes = await phantom.screenshot_agent.page.screenshot(type="png")
        phantom.current_ui_state = await phantom.analyzer_agent.analyze_screenshot(
            screenshot_bytes
        )

        # Initialiser l'Action Agent
        phantom.action_agent = ActionAgent(
            page=phantom.screenshot_agent.page,
            analyzer=phantom.analyzer_agent,
        )

        # Broadcast aux WebSocket clients
        await broadcast({
            "type": "session_started",
            "session_id": session_id,
            "url": req.url,
            "ui_state": json.loads(phantom.current_ui_state.to_json()),
        })

        return {
            "session_id": session_id,
            "url": req.url,
            "elements_found": len(phantom.current_ui_state.elements),
            "page_context": phantom.current_ui_state.page_context,
            "ui_state": json.loads(phantom.current_ui_state.to_json()),
        }
    except Exception as e:
        logger.error(f"❌ Crash au démarrage de la session : {e}")
        # Nettoyage radical en cas d'erreur
        phantom.is_running = False
        phantom.session_id = None
        if phantom.screenshot_agent:
            try:
                await phantom.screenshot_agent.close()
            except:
                pass
        raise HTTPException(500, f"Erreur de démarrage: {str(e)}")


@app.post("/api/session/stop")
async def stop_session():
    """Arrête la session en cours."""
    if not phantom.is_running and phantom.session_id is None:
        return {"status": "already_stopped"}

    logger.info("⏹️ Arrêt de la session...")
    try:
        if phantom.screenshot_agent:
            phantom.screenshot_agent.stop_capture_loop()
            await phantom.screenshot_agent.close()
    except Exception as e:
        logger.error(f"⚠️ Erreur lors de la fermeture du navigateur: {e}")
    finally:
        phantom.is_running = False
        phantom.session_id = None
        phantom.screenshot_agent = None
        phantom.action_agent = None
        
        await broadcast({"type": "session_stopped"})

    return {"status": "stopped"}


@app.post("/api/command")
async def execute_command(req: CommandRequest):
    """
    Commande principale — l'utilisateur dit ce qu'il veut.
    Phantom planifie et exécute.
    """
    if not phantom.is_running:
        raise HTTPException(400, "Aucune session active. Démarrez-en une d'abord.")

    logger.info(f"🎯 Commande reçue : '{req.intent}'")

    # Capturer l'état UI actuel
    screenshot = await phantom.screenshot_agent.page.screenshot(type="png")
    ui_state = await phantom.analyzer_agent.analyze_screenshot(screenshot)
    phantom.current_ui_state = ui_state

    # Générer le plan
    plan = await phantom.action_agent.generate_plan(req.intent, ui_state)

    if not plan.steps:
        return {
            "status": "no_plan",
            "message": "Impossible de générer un plan pour cette intention.",
        }

    result = {
        "intent": req.intent,
        "plan": {
            "total_steps": plan.total_steps,
            "steps": [
                {
                    "action": s.action_type,
                    "target": s.target_description,
                    "risk": s.risk_level,
                }
                for s in plan.steps
            ],
        },
    }

    if req.auto_execute:
        # Callback narration → broadcast WebSocket
        async def narrate(text):
            payload = {"type": "narration", "text": text}
            if phantom.accessibility_mode:
                audio_b64 = await generate_tts_audio(text)
                if audio_b64:
                    payload["audio"] = audio_b64
            await broadcast(payload)

        phantom.action_agent.set_narration_callback(narrate)

        # Exécuter le plan
        step_results = await phantom.action_agent.execute_plan(plan, ui_state)

        # Screenshot final
        final_screenshot = await phantom.screenshot_agent.page.screenshot(type="png")
        final_state = await phantom.analyzer_agent.analyze_screenshot(final_screenshot)
        phantom.current_ui_state = final_state

        result["execution"] = {
            "completed": True,
            "steps_succeeded": sum(1 for r in step_results if r.success),
            "steps_total": len(step_results),
            "final_state": json.loads(final_state.to_json()),
        }

        await broadcast({
            "type": "execution_complete",
            "result": result["execution"],
        })

    return result


@app.post("/api/action")
async def execute_single_action(req: ActionRequest):
    """Exécute une action unique (mode direct)."""
    if not phantom.is_running:
        raise HTTPException(400, "Aucune session active.")

    result = await phantom.action_agent.execute_single_action(
        action_type=req.action_type,
        target=req.target,
        value=req.value,
        ui_state=phantom.current_ui_state,
    )

    return {
        "success": result.success,
        "action": result.action_performed,
        "narration": result.narration,
        "error": result.error,
    }


@app.get("/api/state")
async def get_current_state():
    """Retourne l'état UI actuel."""
    if not phantom.is_running:
        return {"status": "no_session"}

    # Re-capture fraîche
    screenshot = await phantom.screenshot_agent.page.screenshot(type="png")
    ui_state = await phantom.analyzer_agent.analyze_screenshot(screenshot)
    phantom.current_ui_state = ui_state

    return {
        "session_id": phantom.session_id,
        "ui_state": json.loads(ui_state.to_json()),
        "elements_count": len(ui_state.elements),
    }


@app.get("/api/screenshot")
async def get_screenshot():
    """Retourne le screenshot actuel en base64 pour le frontend."""
    import base64

    if not phantom.is_running:
        raise HTTPException(400, "Aucune session active.")

    screenshot = await phantom.screenshot_agent.page.screenshot(type="png")
    b64 = base64.b64encode(screenshot).decode("utf-8")

    return {"image": f"data:image/png;base64,{b64}"}


@app.post("/api/navigate")
async def navigate(url: str):
    """Navigue vers une nouvelle URL."""
    if not phantom.is_running:
        raise HTTPException(400, "Aucune session active.")

    await phantom.screenshot_agent.navigate(url)

    # Re-analyser
    screenshot = await phantom.screenshot_agent.page.screenshot(type="png")
    ui_state = await phantom.analyzer_agent.analyze_screenshot(screenshot)
    phantom.current_ui_state = ui_state

    return {
        "url": url,
        "elements_found": len(ui_state.elements),
        "page_context": ui_state.page_context,
    }


# ── WebSocket ────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """
    WebSocket bidirectionnel pour la communication temps réel.
    Le frontend envoie des commandes vocales transcrites.
    Le backend envoie des updates d'état, narrations, screenshots.
    """
    await ws.accept()
    phantom.connected_clients.append(ws)
    logger.info(f"🔌 Client WebSocket connecté ({len(phantom.connected_clients)} total)")

    try:
        while True:
            data = await ws.receive_text()
            message = json.loads(data)

            match message.get("type"):
                case "command":
                    # Commande vocale ou texte
                    intent = message.get("intent", "")
                    if intent:
                        # If no session, auto-start one with smart URL
                        if not phantom.is_running:
                            asyncio.create_task(
                                _auto_navigate_and_execute(intent)
                            )
                        else:
                            asyncio.create_task(
                                _handle_ws_command(intent)
                            )

                case "set_accessibility":
                    # Toggle Navigator for All mode
                    mode = message.get("enabled", False)
                    phantom.accessibility_mode = mode
                    logger.info(f"♿ Accessibility Mode: {'ON' if mode else 'OFF'}")
                    
                    if mode and phantom.current_ui_state:
                        # Décrire immédiatement la page actuelle pour l'utilisateur
                        text = f"Accessibility mode activated. I see a page with {len(phantom.current_ui_state.elements)} interactive elements."
                        
                        async def _send_welcome(text_msg):
                            payload = {"type": "narration", "text": text_msg}
                            audio_b64 = await generate_tts_audio(text_msg)
                            if audio_b64:
                                payload["audio"] = audio_b64
                            await broadcast(payload)
                            
                        asyncio.create_task(_send_welcome(text))

                case "pause":
                    if phantom.action_agent:
                        phantom.action_agent.pause()
                        await ws.send_json({"type": "paused"})

                case "resume":
                    if phantom.action_agent:
                        phantom.action_agent.resume()
                        await ws.send_json({"type": "resumed"})

                case "screenshot":
                    # Demande un screenshot frais
                    if phantom.is_running:
                        import base64
                        screenshot = await phantom.screenshot_agent.page.screenshot(
                            type="png"
                        )
                        b64 = base64.b64encode(screenshot).decode("utf-8")
                        await ws.send_json({
                            "type": "screenshot",
                            "image": f"data:image/png;base64,{b64}",
                        })

    except WebSocketDisconnect:
        phantom.connected_clients.remove(ws)
        logger.info(f"🔌 Client déconnecté ({len(phantom.connected_clients)} restants)")


async def _auto_navigate_and_execute(intent: str):
    """Auto-start a session with a smart URL inferred from the user's intent."""
    try:
        await broadcast({"type": "narration", "text": "🧠 Let me figure out the best website for that..."})

        # Ask Gemini to infer the best URL
        url = await _infer_url(intent)
        if not url:
            url = "https://www.google.com"

        await broadcast({"type": "narration", "text": f"🌐 Navigating to {url}..."})
        await broadcast({"type": "auto_navigate", "url": url})

        # Start session programmatically
        phantom.screenshot_agent = ScreenshotAgent()
        phantom.analyzer_agent = AnalyzerAgent()
        phantom.action_agent = ActionAgent()
        phantom.session_id = str(uuid.uuid4())

        await phantom.screenshot_agent.start_browser(url)
        screenshot = await phantom.screenshot_agent.take_screenshot()
        page_url = phantom.screenshot_agent.page.url
        ui_state = await phantom.analyzer_agent.analyze_screenshot(screenshot)
        phantom.current_ui_state = ui_state
        phantom.is_running = True

        import base64 as b64mod
        encoded = b64mod.b64encode(screenshot).decode("utf-8")
        await broadcast({
            "type": "screenshot",
            "image": f"data:image/png;base64,{encoded}",
        })
        await broadcast({"type": "session_auto_started", "url": url, "elements": len(ui_state.elements)})

        # Now execute the command on this page
        await _handle_ws_command(intent)

    except Exception as e:
        logger.error(f"❌ Auto-navigate error: {e}")
        await broadcast({"type": "error", "message": str(e)})


async def _infer_url(intent: str) -> Optional[str]:
    """Ask Gemini to infer the best URL to navigate to based on user intent."""
    try:
        from agents.gemini_utils import get_gemini_client
        client = get_gemini_client()

        prompt = f"""Given this user request: \"{intent}\"

What is the single best URL to navigate to? Return ONLY the URL, nothing else.

Rules:
- For flights → https://www.google.com/travel/flights
- For maps/directions/restaurants → https://www.google.com/maps
- For shopping/products → https://www.google.com/shopping
- For news/articles/information → https://www.google.com
- For hotels → https://www.google.com/travel/hotels
- For weather → https://www.google.com
- For specific websites mentioned by the user → use that exact URL
- Default → https://www.google.com

Return ONLY the URL:"""

        response = await asyncio.to_thread(
            client.models.generate_content,
            model=settings.gemini_model,
            contents=prompt,
        )
        url = response.text.strip() if response.text else "https://www.google.com"
        # Clean up — remove markdown or quotes
        url = url.replace("`", "").replace('"', '').replace("'", "").strip()
        if not url.startswith("http"):
            url = "https://www.google.com"
        return url
    except Exception as e:
        logger.error(f"❌ URL inference error: {e}")
        return "https://www.google.com"


async def _handle_ws_command(intent: str):
    """Gère une commande reçue par WebSocket — mode agent conversationnel."""
    try:
        # Natural opening — the agent acknowledges the request
        await broadcast({
            "type": "narration",
            "text": f"🧠 Let me understand the page and figure out how to do that..."
        })

        screenshot = await phantom.screenshot_agent.page.screenshot(type="png")
        ui_state = await phantom.analyzer_agent.analyze_screenshot(screenshot)

        await broadcast({
            "type": "context_update",
            "context": ui_state.page_context,
            "elements_count": len(ui_state.elements),
        })

        # Generate plan silently — no robotic step listing
        plan = await phantom.action_agent.generate_plan(intent, ui_state)

        # Send a natural description of what the agent will do
        if plan.steps:
            await broadcast({
                "type": "narration",
                "text": f"👁️ I can see {len(ui_state.elements)} interactive elements. Working on it..."
            })

            async def narrate(text):
                payload = {"type": "narration", "text": text}
                if phantom.accessibility_mode:
                    audio_b64 = await generate_tts_audio(text)
                    if audio_b64:
                        payload["audio"] = audio_b64
                await broadcast(payload)

            phantom.action_agent.set_narration_callback(narrate)

            # Execute silently — the agent just acts
            results = await phantom.action_agent.execute_plan(plan, ui_state)

            # === THE KEY FEATURE: Post-execution result analysis ===
            # Take a fresh screenshot of the result page and ask Gemini
            # to read and summarize what's visible, like a human would
            final_screenshot = await phantom.screenshot_agent.page.screenshot(type="png")
            final_b64 = base64.b64encode(final_screenshot).decode("utf-8")

            # Send the updated screenshot
            await broadcast({
                "type": "screenshot",
                "image": f"data:image/png;base64,{final_b64}",
            })

            # Ask Gemini to summarize the results conversationally
            result_summary = await _summarize_results(final_screenshot, intent)
            if result_summary:
                payload = {"type": "result_summary", "text": result_summary}
                if phantom.accessibility_mode:
                    audio_b64 = await generate_tts_audio(result_summary)
                    if audio_b64:
                        payload["audio"] = audio_b64
                await broadcast(payload)

            # Update UI state
            final_state = await phantom.analyzer_agent.analyze_screenshot(final_screenshot)
            phantom.current_ui_state = final_state

            await broadcast({
                "type": "execution_complete",
                "success": all(r.success for r in results),
                "steps_completed": sum(1 for r in results if r.success),
            })

    except Exception as e:
        logger.error(f"❌ Erreur commande WS : {e}")
        await broadcast({"type": "error", "message": str(e)})


async def _summarize_results(screenshot: bytes, user_intent: str) -> Optional[str]:
    """Ask Gemini to read the current page and summarize what it sees
    in a natural, conversational way — like a human assistant would."""
    try:
        from agents.gemini_utils import get_gemini_client
        from google.genai import types

        client = get_gemini_client()
        b64_img = base64.b64encode(screenshot).decode("utf-8")

        prompt = f"""You are Phantom, a friendly AI assistant navigating the web for a user.
The user asked: "{user_intent}"

Look at this screenshot of the current page state AFTER I performed actions.
Describe what you see in a natural, conversational way as if you were a helpful friend.

RULES:
- Be concise but informative (2-4 sentences max)
- If there are results (flights, search results, listings, products), mention the top 2-3 options with key details (price, time, name)
- If the page shows an error or nothing changed, say so honestly
- Speak directly to the user: "I found...", "Here's what I see...", "The page shows..."
- Do NOT mention technical details (screenshots, steps, elements)
- Do NOT say "I executed 3 steps" — just describe what happened naturally
- Use a friendly, helpful tone

Example good responses:
- "I found 3 flights from Paris to Dubai. The cheapest is with Emirates at $420, departing at 2:15 PM. Air France also has one at $510 at 6:00 PM."
- "Here are the directions — it's about 25 minutes by car from Manhattan to JFK. The fastest route is via I-278."
- "I searched for that and here are the top results. The first article is from Reuters about the latest speech."
"""

        response = await asyncio.to_thread(
            client.models.generate_content,
            model=settings.gemini_model,
            contents=[
                types.Content(parts=[
                    types.Part.from_text(text=prompt),
                    types.Part.from_bytes(data=screenshot, mime_type="image/png"),
                ])
            ],
        )
        return response.text.strip() if response.text else None
    except Exception as e:
        logger.error(f"❌ Error summarizing results: {e}")
        return None


async def broadcast(message: dict):
    """Envoie un message à tous les clients WebSocket connectés."""
    disconnected = []
    for client in phantom.connected_clients:
        try:
            await client.send_json(message)
        except Exception:
            disconnected.append(client)
    for client in disconnected:
        phantom.connected_clients.remove(client)


# ── Entrypoint ───────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.api_reload,
    )

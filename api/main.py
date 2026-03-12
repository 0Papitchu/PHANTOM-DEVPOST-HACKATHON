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
        raise HTTPException(400, "Session déjà active. Arrêtez-la d'abord.")

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
                    if intent and phantom.is_running:
                        # Exécuter en arrière-plan
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


async def _handle_ws_command(intent: str):
    """Gère une commande reçue par WebSocket."""
    try:
        # Progress: scanning
        await broadcast({"type": "progress", "step": "analyzing", "message": "👁️ Scanning UI..."})

        screenshot = await phantom.screenshot_agent.page.screenshot(type="png")
        ui_state = await phantom.analyzer_agent.analyze_screenshot(screenshot)

        await broadcast({
            "type": "progress",
            "step": "analyzed",
            "message": f"✅ {len(ui_state.elements)} elements detected",
        })

        # Progress: planning
        await broadcast({"type": "progress", "step": "planning", "message": "🧠 Generating action plan..."})

        plan = await phantom.action_agent.generate_plan(intent, ui_state)

        await broadcast({
            "type": "plan_generated",
            "intent": intent,
            "steps": [
                {"action": s.action_type, "target": s.target_description}
                for s in plan.steps
            ],
        })

        if plan.steps:
            async def narrate(text):
                payload = {"type": "narration", "text": text}
                if phantom.accessibility_mode:
                    audio_b64 = await generate_tts_audio(text)
                    if audio_b64:
                        payload["audio"] = audio_b64
                await broadcast(payload)

            phantom.action_agent.set_narration_callback(narrate)

            # Progress: executing
            await broadcast({
                "type": "progress",
                "step": "executing",
                "message": f"⚡ Executing {len(plan.steps)} step(s)...",
            })

            results = await phantom.action_agent.execute_plan(plan, ui_state)

            # Update final screenshot for frontend
            final_screenshot = await phantom.screenshot_agent.page.screenshot(type="png")
            final_b64 = base64.b64encode(final_screenshot).decode("utf-8")

            await broadcast({
                "type": "execution_complete",
                "success": all(r.success for r in results),
                "steps_completed": sum(1 for r in results if r.success),
            })

            # Send updated screenshot
            await broadcast({
                "type": "screenshot",
                "image": f"data:image/png;base64,{final_b64}",
            })

    except Exception as e:
        logger.error(f"❌ Erreur commande WS : {e}")
        await broadcast({"type": "error", "message": str(e)})


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

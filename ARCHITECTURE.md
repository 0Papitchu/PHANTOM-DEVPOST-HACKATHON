# ARCHITECTURE — Phantom UI Navigator

## Vue d'ensemble

```
┌──────────────┐     WebSocket      ┌──────────────────────────────┐
│   Frontend   │◄──────────────────►│   FastAPI (api/main.py)      │
│   (HTML/JS)  │     REST API       │                              │
└──────────────┘                    │  ┌────────────────────────┐  │
                                    │  │   ADK Orchestrator     │  │
                                    │  │   (adk_agents.py)      │  │
                                    │  └──────────┬─────────────┘  │
                                    │             │ coordinates    │
                                    │  ┌──────────▼─────────────┐  │
                                    │  │   Screenshot Agent     │  │
                                    │  │   Playwright → GCS     │  │
                                    │  └──────────┬─────────────┘  │
                                    │             │ screenshot      │
                                    │  ┌──────────▼─────────────┐  │
                                    │  │   Analyzer Agent       │  │
                                    │  │   Gemini Vision →      │  │
                                    │  │   UIState (JSON)       │  │
                                    │  └──────────┬─────────────┘  │
                                    │             │ ui_state        │
                                    │  ┌──────────▼─────────────┐  │
                                    │  │   Action Agent         │  │
                                    │  │   Plan → Execute →     │  │
                                    │  │   Validate (coords)    │  │
                                    │  └────────────────────────┘  │
                                    └──────────────────────────────┘
                                                 │
                                    ┌────────────▼─────────────────┐
                                    │   Google Cloud               │
                                    │   - Cloud Run (hosting)      │
                                    │   - Cloud Storage (screens)  │
                                    │   - Firestore (sessions)     │
                                    │   - Pub/Sub (events)         │
                                    │   - Secret Manager (keys)    │
                                    │   - Text-to-Speech (a11y)    │
                                    └──────────────────────────────┘
```

## Couches d'architecture

```
Frontend (HTML/JS)          ← UI + WebSocket client + Voice I/O
     ↓
API Layer (FastAPI)          ← REST + WebSocket + session management
     ↓
ADK Layer (google.adk)       ← LlmAgent orchestration (Vision + Planner)
     ↓
Agent Layer                  ← Screenshot / Analyzer / Action agents
     ↓
Shared Utilities             ← gemini_utils (singleton client + retry/backoff)
     ↓
Infrastructure               ← GCP services (Storage, Firestore, Pub/Sub, TTS)
```

## Flux de données

1. **User** → parle ou tape une commande
2. **Frontend** → transcription vocale (Web Speech API) → WebSocket
3. **API** → reçoit l'intent (et le statut `accessibility_mode`) → déclenche le pipeline
4. **Progress broadcast** → `analyzing` → `planning` → `executing` (temps réel via WS)
5. **Screenshot Agent** → capture l'écran via Playwright
6. **Analyzer Agent** → envoie à Gemini Vision (via `gemini_generate_with_retry`) → retourne UIState JSON
7. **Action Agent** → génère un plan (via `gemini_generate_with_retry`) → exécute étape par étape (coordonnées visuelles)
8. **Validation** → re-capture → compare UIState → confirme succès
9. **Narration (Standard)** → Web Speech API côté navigateur
10. **Narration (Accessibility Mode)** → **Google Cloud TTS** (backend génère MP3 Base64) → streamé via WebSocket → lecture native `new Audio()` côté frontend.

## Principe fondamental

**Zero DOM access.** L'agent ne touche jamais au code source, au DOM, ni aux APIs de l'app cible. Toutes les interactions passent par les coordonnées visuelles, exactement comme un humain utiliserait l'écran.

## Fichiers par couche

| Couche | Fichiers | Responsabilité |
|--------|----------|----------------|
| Config | `config/settings.py` | Variables d'env, singleton Settings |
| Shared | `agents/gemini_utils.py` | Singleton Gemini client + retry/backoff |
| ADK | `agents/adk_agents.py` | LlmAgent wrappers (Orchestrator, Vision, Planner) |
| Agents | `agents/screenshot_agent.py` | Playwright + GCS + Pub/Sub |
| Agents | `agents/analyzer_agent.py` | Gemini Vision + UIState + diff |
| Agents | `agents/action_agent.py` | Planner + Executor + validation |
| API | `api/main.py` | FastAPI + WS + orchestration + TTS |
| Frontend | `frontend/index.html` | UI cyberpunk + overlay + voice |

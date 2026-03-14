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

1. **User** → parle ou tape une commande (ex: "Find flights Paris to Dubai")
2. **Frontend** → transcription vocale (Web Speech API) ou texte → WebSocket
3. **API** → si **pas de session active** : `_infer_url()` demande à Gemini le meilleur URL → `_auto_navigate_and_execute()` crée la session
4. **Screenshot Agent** → capture l'écran via Playwright
5. **Analyzer Agent** → envoie à Gemini Vision (via `gemini_generate_with_retry`) → retourne UIState JSON
6. **Action Agent** → génère un plan (silencieux) → exécute étape par étape (coordonnées visuelles)
7. **Result Summarizer** → `_summarize_results()` prend un screenshot du résultat → Gemini lit et décrit conversationnellement ("I found 3 flights...")
8. **Narration** → résultat envoyé comme `result_summary` via WebSocket → affiché en bulle dans le sidebar + voix TTS
9. **Continuation** → l'utilisateur peut interrompre à tout moment avec une nouvelle commande → re-loop étape 2

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
| Agents | `agents/action_agent.py` | Planner + Executor + narration conversationnelle |
| API | `api/main.py` | FastAPI + WS + auto-navigation + result summarizer + TTS |
| Frontend | `frontend/index.html` | UI cyberpunk + overlay + voice |
| Frontend | `frontend/app.js` | WebSocket client + auto-session + result display |

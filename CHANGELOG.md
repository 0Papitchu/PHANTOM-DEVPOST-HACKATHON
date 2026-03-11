# CHANGELOG — Phantom UI Navigator

## [v0.5.0] — 2026-03-06 — ANTIGRAVITY

### Ajouté
- `agents/gemini_utils.py` — Singleton Gemini client + retry with exponential backoff (3 tentatives, 1s→2s→4s)
- `agents/adk_agents.py` — Intégration Google ADK avec 3 LlmAgents (Orchestrator, VisionAnalyzer, ActionPlanner)
- `google-adk` ajouté à `requirements.txt`
- Progress broadcasts en temps réel dans le WebSocket (analyzing → planning → executing)
- Screenshot automatique post-exécution envoyé au frontend
- `adk_agents` field dans `PhantomState`

### Modifié
- `analyzer_agent.py` — Utilise `gemini_utils.get_gemini_client()` au lieu de créer son propre client
- `action_agent.py` — Utilise `gemini_generate_with_retry()` au lieu de recréer un client à chaque appel
- `api/main.py` — ADK init dans lifespan, progress broadcasts dans `_handle_ws_command`

### Corrigé
- **BUG CRITIQUE** : `_handle_ws_command` ligne 491 référençait `step.target_element` qui n'existe pas sur `ActionStep` → crash à chaque commande WebSocket contenant un click
- Null check après retry exhaustion dans `analyze_screenshot()` et `generate_plan()`

### ⚠️ Breaking Changes
- `AnalyzerAgent._init_gemini_client()` et `_get_secret()` supprimés → remplacés par `gemini_utils.get_gemini_client()`

---

## [v0.4.0] — 2026-03-01 — ANTIGRAVITY

### Ajouté
- **Navigator for All** — Accessibility pivot avec Google Cloud TTS
- Voix Neural2-D premium pour la narration
- Toggle Accessibility Mode dans le frontend
- WS message `set_accessibility` pour activer/désactiver

### Modifié
- Frontend : Ajout du toggle A11y + notifications audio
- api/main.py : Intégration TTS async + narration callback

---

## [v0.3.0] — 2026-03-01 — ANTIGRAVITY

### Ajouté
- Déploiement Cloud Run fonctionnel
- Dockerfile multi-stage avec Playwright
- WSS auto-detection pour Cloud Run

### Corrigé
- WebSocket protocol switch (ws:// vs wss://)

---

## [v0.2.0] — 2026-02-28 — ANTIGRAVITY

### Ajouté
- Action Agent complet — 11 types d'actions (click, type, scroll, hover, key_press, etc.)
- Système de pause/resume pour les plans
- Validation post-action par re-analyse

---

## [v0.1.0] — 2026-02-26 — ANTIGRAVITY

### Ajouté
- Setup initial du projet
- Screenshot Agent (Playwright + GCS + Pub/Sub)
- Analyzer Agent (Gemini Vision)
- FastAPI backend avec WebSocket
- Frontend cyberpunk
- Config centralisée (pydantic-settings)

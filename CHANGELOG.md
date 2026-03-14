# CHANGELOG — Phantom UI Navigator

## [v0.8.0] — 2026-03-14 — ANTIGRAVITY

### Ajouté
- **Interactive Option Cards** — `_summarize_results` génère maintenant un JSON structuré avec `text` + `options[]` (titre, sous-titre, icône). Le frontend affiche des boutons-cartes interactifs avec icône à gauche et flèche au survol.
- **Smart URL Navigation** — `_handle_ws_command` détecte automatiquement si l'intent de l'utilisateur nécessite un autre site web et navigue en conséquence (ex: passer de Google Flights à Google Hotels).
- **URL Bar Sync** — Broadcast `auto_navigate` après chaque action pour synchroniser la barre d'URL du frontend.

### Corrigé
- **`_summarize_results` INVALID_ARGUMENT** — La fonction utilisait `types.Content(parts=[...])` au lieu d'une liste plate de `Part` objects, causant un crash silencieux. Ajouté `response_mime_type='application/json'`.
- **Narration robotique** — Remplacé "Let me understand the page..." par des messages naturels : "Got it. Let me handle that for you."
- **Result summary toujours exécuté** — Le résumé avec options est maintenant généré même si les étapes d'action échouent.

## [v0.7.0] — 2026-03-14 — ANTIGRAVITY

### Ajouté
- **UI Split-Screen Overhaul** — Refonte complète de l'interface en utilisant Tailwind CSS (via CDN) pour un rendu "Premium SaaS / AI". L'interface est désormais divisée en deux paires : un Viewport à gauche (flex-1) et un Command Center à droite (450px fixes).
- **Log Types Stricts & Animations** — Implémentation de 4 types de logs stricts (`user`, `agent`, `action`, `system`) avec des badges et typographies spécifiques. Ajout d'animations CSS fluides (`animate-slide-up`) imitant Framer Motion.
- **Interactive Options Cards** — Support de l'affichage de "cartes interactives" quand l'agent propose des options (avec hover states et boutons). La méthode `handleOptionClick()` renvoie le choix à l'agent en natif.
- **Pulsing Mic Radar** — Ajout d'une animation radar concentrique gérée par CSS lors de l'enregistrement vocal (activation du micro).
- **Custom Scrollbars & Design System** — Palette de couleurs adoucies (`slate-50`, `slate-900`) et accetuation via `bahama-blue`.

## [v0.7.1] — 2026-03-14 — ANTIGRAVITY

### Corrigé
- **app.js 404 sur deploy** — Le `<script src="app.js">` référençait la racine, mais FastAPI monte les fichiers frontend sous `/static/`. Corrigé vers `<script src="/static/app.js">`.
- **DOM references cassées** — Supprimé les refs à `scanLine` et `stopBtn` (éléments inexistants dans le nouveau HTML). Ajouté `statusDot`, `elementCountBadge` corrects.
- **setStatus() écrasait les classes Tailwind** — Réécrit avec les bonnes classes CSS pour `online`/`offline`.
- **switchTab() cherchait `.panel-tab`** — Réécrit pour utiliser `#tabActivity` / `#tabElements` (nouveaux IDs).
- **startBtn.textContent écrasait les spans internes** — Créé `updateStartBtn()` helper pour modifier `#startIcon` et `#startText` proprement.
- **displayScreenshot() n'enlevait pas `.hidden`** — Ajouté `classList.remove('hidden')`.

## [v0.6.0] — 2026-03-14 — ANTIGRAVITY

### Ajouté
- **Smart Auto-Navigation** — L'utilisateur n'a plus besoin d'entrer un URL. Phantom utilise Gemini pour détecter le meilleur site web à partir de l'intent (ex: "Find flights Paris to Dubai" → google.com/travel/flights). Nouvelles fonctions `_auto_navigate_and_execute()` et `_infer_url()` dans `api/main.py`.
- **Post-Execution Result Summarizer** — Après chaque action, Gemini analyse le screenshot du résultat et présente les informations de manière conversationnelle (ex: "I found 3 flights. The cheapest is Emirates at $420"). Nouvelle fonction `_summarize_results()` dans `api/main.py`.
- **Result Bubble CSS** — Style distinct `.log-entry.result` avec gradient et taille de texte plus grande pour les résumés de résultats dans le sidebar.
- Nouveaux types WebSocket : `result_summary`, `context_update`, `auto_navigate`, `session_auto_started`.

### Modifié
- `agents/action_agent.py` — Narration humaine : "Got it. Let me handle that for you." au lieu de "Étape 1/3 : type sur..." + exécution silencieuse (pas d'annonce par étape).
- `api/main.py` — `_handle_ws_command()` réécrit pour un flow conversationnel. Suppression des messages robotiques `progress` et `plan_generated` avec liste d'étapes.
- `frontend/app.js` — `sendCommand()` fonctionne sans session active (auto-connection WebSocket + loading animation). Handlers pour `result_summary`, `context_update`, `auto_navigate`, `session_auto_started`.
- `frontend/index.html` — Placeholder mis à jour : "Just tell Phantom what you need" + "e.g. Find flights from Paris to Dubai..."

### Corrigé
- Bug de syntaxe double `await self._narrate()` imbriqué dans `action_agent.py`
- Suppression de `commandInput.value = ''` en double dans `app.js`

---

## [v0.5.1] — 2026-03-12 — ANTIGRAVITY

### Modifié
- `agents/gemini_utils.py` — Migration vers Vertex AI via Application Default Credentials (ADC) pour utiliser les crédits GCP ($300 tiers) et esquiver les quotas stricts d'AI Studio.

### Corrigé
- **BUG CRITIQUE** : Gemini 2.0 sur Vertex AI retourne parfois le JSON dans des blocs markdown (````json ... ````). Modifié `analyzer_agent.py` pour stripper ces balises et éviter une `JSONDecodeError`.
- **BUG TRONCATURE** : Passage de `max_output_tokens` à `8192` et implémentation d'une auto-réparation "best-effort" pour les tableaux JSON d'éléments coupés prématurément.
- **DEADLOCK SESSION** : Fixé un bug dans `api/main.py` causant l'erreur "Session déjà active". Les endpoints `/api/session/start` et `stop` sont maintenant encapsulés dans un try/finally strict garantissant la clôture du process `Playwright`.

---

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

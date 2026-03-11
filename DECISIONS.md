# DECISIONS — Phantom UI Navigator

## [2026-03-06] — ADK Integration Strategy

**Contexte:** Le hackathon demande l'utilisation du Google ADK (Agent Development Kit). google-adk était installé depuis le début (v0.3.0) mais jamais intégré dans le code. Un juge regardant le code verrait un red flag.

**Décision:** Créer `agents/adk_agents.py` avec 3 `LlmAgent` ADK (Orchestrator → VisionAnalyzer + ActionPlanner) qui wrappent notre pipeline custom. L'exécution Playwright reste dans nos agents custom car ADK ne gère pas nativement le browser automation.

**Alternatives rejetées:**
- Tout réécrire avec ADK natif → trop risqué à 10 jours de la deadline, et ADK ne supporte pas directement Playwright
- Retirer ADK des deps → perdre un critère de scoring

**Impact:** `agents/adk_agents.py` (NEW), `api/main.py` (lifespan), `requirements.txt`

---

## [2026-03-06] — Singleton Gemini Client + Retry

**Contexte:** `action_agent.py` recréait un `genai.Client()` à chaque appel de `generate_plan()`. Pas de retry en cas d'erreur API transitoire.

**Décision:** Centraliser dans `agents/gemini_utils.py` : singleton client, retry 3 tentatives avec backoff exponentiel (1s→2s→4s), gestion des status codes retryables (429, 500, 503).

**Alternatives rejetées:**
- Retry dans chaque agent → duplication, maintenance difficile
- Pas de retry → risque d'échec en démo live

**Impact:** `agents/gemini_utils.py` (NEW), `agents/analyzer_agent.py`, `agents/action_agent.py`

---

## [2026-03-01] — WSS pour Cloud Run

**Contexte:** Cloud Run force HTTPS. Le frontend utilisait `ws://` en dur.

**Décision:** Auto-detection du protocole dans le frontend : `window.location.protocol === 'https:' ? 'wss://' : 'ws://'`

**Alternatives rejetées:**
- Variable d'environnement → pas flexible selon l'environnement de déploiement
- Proxy nginx → complexité inutile

**Impact:** `frontend/index.html`

---

## [2026-03-01] — Accessibility Pivot — GCP TTS

**Contexte:** Besoin de différenciation pour le hackathon. L'accessibilité est un multiplicateur de crédibilité devant les juges Google.

**Décision:** Intégrer Google Cloud Text-to-Speech (Neural2-D voice) pour narrer les actions de Phantom. Toggle Accessibility Mode dans le frontend.

**Alternatives rejetées:**
- Web Speech API browser → voix robotique, pas impressionnant
- ElevenLabs → pas GCP natif, pas de bonus scoring

**Impact:** `api/main.py`, `frontend/index.html`

---

## [2026-02-28] — Zero DOM Access

**Contexte:** Choix architectural fondamental — comment interagir avec les UIs cibles.

**Décision:** Toutes les interactions passent par coordonnées visuelles (x, y pixels). Playwright utilise `mouse.click(x, y)`, jamais `querySelector` ni CSS selectors.

**Alternatives rejetées:**
- DOM scraping → ne marche pas sur les legacy apps, desktop apps
- Accessibility tree → dépend de l'OS, pas universel

**Impact:** Architecture complète du projet

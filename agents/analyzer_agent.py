# phantom-ui-navigator/agents/analyzer_agent.py
"""
Analyzer Agent — Vision Pipeline avec Gemini.
Analyse les screenshots, extrait l'état UI structuré,
détecte les changements entre frames.

PRINCIPE FONDAMENTAL : Zéro accès DOM.
On ne touche JAMAIS au code source de l'app cible.
Tout passe par la vision — comme un humain devant l'écran.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

from google.cloud import pubsub_v1, storage
from google.genai import types

from agents.gemini_utils import get_gemini_client, gemini_generate_with_retry

from config.settings import settings

logger = logging.getLogger("phantom.analyzer")


# ── Modèles de données ──────────────────────────────────────

@dataclass
class UIElement:
    """Un élément interactif détecté visuellement."""
    element_id: str
    element_type: str          # button, input, link, dropdown, menu, checkbox, etc.
    label: str                 # texte visible ou description
    x: int                     # position x (pixels)
    y: int                     # position y (pixels)
    width: int
    height: int
    state: str                 # enabled, disabled, focused, checked, selected
    confidence: float          # 0.0 → 1.0

    @property
    def center_x(self) -> int:
        return self.x + self.width // 2

    @property
    def center_y(self) -> int:
        return self.y + self.height // 2


@dataclass
class UIState:
    """État complet de l'interface à un instant T."""
    app_name: str = ""
    page_title: str = ""
    page_context: str = ""       # description du workflow/écran visible
    elements: list[UIElement] = field(default_factory=list)
    error_messages: list[str] = field(default_factory=list)
    raw_description: str = ""    # description textuelle libre par Gemini
    timestamp: str = ""
    screenshot_uri: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> "UIState":
        elements = [UIElement(**el) for el in data.get("elements", [])]
        return cls(
            app_name=data.get("app_name", ""),
            page_title=data.get("page_title", ""),
            page_context=data.get("page_context", ""),
            elements=elements,
            error_messages=data.get("error_messages", []),
            raw_description=data.get("raw_description", ""),
            timestamp=data.get("timestamp", ""),
            screenshot_uri=data.get("screenshot_uri", ""),
        )


@dataclass
class UIChange:
    """Un changement détecté entre deux UIState."""
    change_type: str   # appeared, disappeared, moved, state_changed, text_changed
    element_label: str
    details: str


# ── Analyzer Agent ───────────────────────────────────────────

VISION_PROMPT = """You are PHANTOM's Vision System. Analyze this screenshot and return a STRUCTURED JSON object.

You must identify ALL interactive UI elements visible on screen. You are looking at a real application — this could be any software: a web app, a desktop app, a legacy system. Your job is to map what a human would see.

Return this EXACT JSON structure:
{
    "app_name": "Name of the application if identifiable",
    "page_title": "Title or header of the current page/view",
    "page_context": "Brief description of what screen/workflow this is (e.g. 'Login page', 'Invoice list with filters', 'Settings panel')",
    "elements": [
        {
            "element_id": "el_1",
            "element_type": "button|input|link|dropdown|menu|checkbox|radio|tab|icon|text_field|search_bar|toggle|slider",
            "label": "visible text or aria description of the element",
            "x": 100,
            "y": 200,
            "width": 120,
            "height": 40,
            "state": "enabled|disabled|focused|checked|selected|filled|empty",
            "confidence": 0.95
        }
    ],
    "error_messages": ["any error or warning messages visible on screen"],
    "raw_description": "A 2-3 sentence natural language description of what you see on screen, as if describing it to a blind person"
}

RULES:
- Coordinates are in PIXELS from top-left corner
- Be PRECISE with bounding boxes — they will be used for clicking
- Include EVERY interactive element, even small icons
- element_id must be unique and sequential (el_1, el_2, ...)
- confidence reflects how sure you are this is an interactive element
- For form inputs (flight search, search bars): set label to the placeholder or prompt text so it can be targeted (e.g. "Where from?", "Where to?", "Search"). If the field shows a current value (e.g. "Chicago, Illinois"), you may include it like "Where from? (Chicago)" or label the field so both placeholder and value are identifiable
- Return ONLY valid JSON, no markdown, no explanation
"""

STATE_DIFF_PROMPT = """Compare these two UI states and identify what changed.

PREVIOUS STATE:
{prev_state}

CURRENT STATE:
{curr_state}

Return a JSON array of changes:
[
    {{
        "change_type": "appeared|disappeared|moved|state_changed|text_changed|new_page",
        "element_label": "which element changed",
        "details": "brief description of the change"
    }}
]

Focus on MEANINGFUL changes that indicate something happened (page navigation, form fill, error appeared, button became disabled, etc).
Return ONLY valid JSON array.
"""


class AnalyzerAgent:
    """
    Agent 2/3 — Analyse visuelle via Gemini.

    Workflow :
    1. Reçoit un screenshot (bytes ou GCS URI)
    2. Envoie à Gemini Vision pour analyse structurée
    3. Retourne un UIState avec tous les éléments interactifs
    4. Peut comparer deux UIState pour détecter les changements
    5. Publie l'analyse sur Pub/Sub (phantom-analysis)
    """

    def __init__(self):
        self.client = get_gemini_client()
        self._previous_state: Optional[UIState] = None
        # Lazy-init GCP clients — allows running without credentials
        self._publisher = None
        self._topic_path = None
        self._storage_client = None
        self._bucket = None

    @property
    def publisher(self):
        if self._publisher is None:
            try:
                self._publisher = pubsub_v1.PublisherClient()
                self._topic_path = self._publisher.topic_path(
                    settings.gcp_project_id, settings.pubsub_topic_analysis
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

    # _init_gemini_client and _get_secret removed — now handled by gemini_utils singleton

    async def analyze_screenshot(self, screenshot_bytes: bytes) -> UIState:
        """
        Analyse un screenshot avec Gemini Vision.
        Retourne un UIState structuré.
        """
        if not self.client:
            logger.warning("⚠️ Gemini client non initialisé — analyse impossible")
            return UIState(raw_description="Gemini client not configured")

        logger.info("👁️ Analyse du screenshot en cours...")

        try:
            response = await gemini_generate_with_retry(
                contents=[
                    types.Part.from_bytes(
                        data=screenshot_bytes,
                        mime_type="image/png",
                    ),
                    VISION_PROMPT,
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,  # Précision maximale
                    max_output_tokens=8192,  # Prévenir la troncature JSON
                ),
            )

            if not response:
                return UIState(raw_description="Gemini API unavailable after retries")

            # Parse la réponse JSON (gère le format markdown ```json de Vertex AI)
            result_text = response.text.strip()
            if result_text.startswith("```json"):
                result_text = result_text[7:]
            if result_text.startswith("```"):
                result_text = result_text[3:]
            if result_text.endswith("```"):
                result_text = result_text[:-3]
            result_text = result_text.strip()

            try:
                result_data = json.loads(result_text)
            except json.JSONDecodeError as e:
                # Si le JSON a été tronqué malgré max_output_tokens, on tente un "repair" basique
                # Parfois Google coupe au milieu du tableau "elements"
                logger.warning(f"⚠️ JSON partiellement tronqué, tentative de réparation... ({e})")
                if '"elements": [' in result_text and not result_text.endswith('}'):
                    # Trouve la dernière accolade fermante valide d'un élément
                    last_obj_end = result_text.rfind('}')
                    if last_obj_end > 0:
                        result_text = result_text[:last_obj_end+1] + "]}"
                        result_data = json.loads(result_text)
                    else:
                        raise e
                else:
                    raise e
                    
            ui_state = UIState.from_dict(result_data)

            logger.info(
                f"✅ Analyse terminée — {len(ui_state.elements)} éléments détectés"
            )
            logger.info(f"📋 Contexte : {ui_state.page_context}")

            return ui_state

        except json.JSONDecodeError as e:
            logger.error(f"❌ Erreur parsing JSON Gemini : {e}")
            logger.error(f"Réponse brute : {response.text[:500]}")
            return UIState(raw_description="Erreur d'analyse — JSON invalide")

        except Exception as e:
            logger.error(f"❌ Erreur Gemini Vision : {e}")
            return UIState(raw_description=f"Erreur d'analyse : {str(e)}")

    async def analyze_from_gcs(self, gcs_uri: str) -> UIState:
        """Analyse un screenshot stocké sur GCS."""
        # Extraire le blob path du GCS URI
        blob_path = gcs_uri.replace(f"gs://{settings.storage_bucket}/", "")
        blob = self.bucket.blob(blob_path)
        screenshot_bytes = blob.download_as_bytes()
        return await self.analyze_screenshot(screenshot_bytes)

    async def detect_changes(
        self,
        prev_state: UIState,
        curr_state: UIState,
    ) -> list[UIChange]:
        """
        Compare deux UIState et détecte les changements significatifs.
        Utilise Gemini pour une compréhension sémantique des diffs.
        """
        logger.info("🔍 Détection des changements UI...")

        prompt = STATE_DIFF_PROMPT.format(
            prev_state=prev_state.to_json(),
            curr_state=curr_state.to_json(),
        )

        try:
            response = await gemini_generate_with_retry(
                contents=[prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.1,
                ),
            )

            if not response:
                return []

            changes_data = json.loads(response.text)
            changes = [UIChange(**c) for c in changes_data]

            logger.info(f"🔄 {len(changes)} changements détectés")
            for ch in changes:
                logger.info(f"   → {ch.change_type}: {ch.element_label} — {ch.details}")

            return changes

        except Exception as e:
            logger.error(f"❌ Erreur détection changements : {e}")
            return []

    async def analyze_and_track(self, screenshot_bytes: bytes) -> tuple[UIState, list[UIChange]]:
        """
        Analyse un screenshot ET détecte les changements par rapport au précédent.
        Retourne (UIState actuel, liste de changements).
        """
        current_state = await self.analyze_screenshot(screenshot_bytes)
        changes = []

        if self._previous_state:
            changes = await self.detect_changes(self._previous_state, current_state)

        self._previous_state = current_state
        return current_state, changes

    async def publish_analysis(self, ui_state: UIState):
        """Publie l'analyse sur Pub/Sub pour les autres agents."""
        if not self.publisher:
            logger.warning("⚠️ Pub/Sub non disponible — publication ignorée")
            return None
        message_data = ui_state.to_json().encode("utf-8")
        future = self.publisher.publish(self._topic_path, data=message_data)
        message_id = future.result(timeout=10)
        logger.info(f"📡 Analyse publiée sur Pub/Sub — message_id={message_id}")
        return message_id

    # Aliases for flight/search form fields so planner labels match Vision output.
    FLIGHT_ORIGIN_ALIASES = (
        "where from",
        "origin",
        "departure",
        "from",
        "flying from",
        "leave from",
        "departure city",
        "origin city",
        "where from?",
    )
    FLIGHT_DESTINATION_ALIASES = (
        "where to",
        "destination",
        "arrival",
        "to",
        "going to",
        "arrival city",
        "destination city",
        "where to?",
    )

    def find_element_by_label(
        self, ui_state: UIState, label: str
    ) -> Optional[UIElement]:
        """
        Trouve un élément par son label — matching multi-stratégie.
        Stratégies : exact > substring > word-overlap > alias (flight forms) > best-effort.
        Utilisé par l'Action Agent pour localiser les cibles.
        """
        label_lower = label.lower().strip()
        label_words = set(label_lower.split())
        best_match = None
        best_score = 0.0

        for el in ui_state.elements:
            el_label_lower = el.label.lower().strip()
            el_words = set(el_label_lower.split())

            # 1. Match exact
            if el_label_lower == label_lower:
                return el

            # 2. Substring match (either direction)
            if label_lower in el_label_lower:
                score = len(label_lower) / max(len(el_label_lower), 1) + 0.5
                if score > best_score:
                    best_score = score
                    best_match = el
                continue
            if el_label_lower in label_lower:
                score = len(el_label_lower) / max(len(label_lower), 1) + 0.3
                if score > best_score:
                    best_score = score
                    best_match = el
                continue

            # 3. Word-level overlap scoring
            if label_words and el_words:
                overlap = len(label_words & el_words)
                if overlap > 0:
                    score = overlap / max(len(label_words), len(el_words))
                    if score > best_score:
                        best_score = score
                        best_match = el

        # 4. Flight form alias match: if label describes origin/destination field,
        # match any element whose label contains a known alias (e.g. Vision returns
        # "Chicago, Illinois" for the origin field; planner says "Where from?").
        if not best_match or best_score < 0.5:
            label_for_aliases = label_lower
            for alias_group, preferred_types in (
                (self.FLIGHT_ORIGIN_ALIASES, ("input", "text_field", "search_bar")),
                (self.FLIGHT_DESTINATION_ALIASES, ("input", "text_field", "search_bar")),
            ):
                if not any(a in label_for_aliases for a in alias_group):
                    continue
                for el in ui_state.elements:
                    el_label_lower = el.label.lower().strip()
                    if any(a in el_label_lower for a in alias_group):
                        type_ok = (el.element_type or "").lower() in preferred_types
                        best_match = el
                        best_score = 0.6 if type_ok else 0.45
                        break
                if best_match and best_score >= 0.45:
                    break

        if best_match and best_score > 0.1:
            logger.info(
                f"🎯 Élément trouvé : '{best_match.label}' "
                f"(score={best_score:.2f}) @ ({best_match.center_x}, {best_match.center_y})"
            )
            return best_match

        # 5. Best-effort fallback — return the highest scoring match regardless of threshold
        if best_match:
            logger.warning(
                f"⚠️ Fallback match pour '{label}' → '{best_match.label}' "
                f"(score={best_score:.2f}) — match de fortune"
            )
            return best_match

        logger.warning(f"⚠️ Élément '{label}' non trouvé dans l'UI ({len(ui_state.elements)} éléments)")
        return None


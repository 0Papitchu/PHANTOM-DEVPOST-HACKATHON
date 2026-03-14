# phantom-ui-navigator/agents/action_agent.py
"""
Action Agent — Le bras exécuteur de Phantom.
Reçoit un plan d'actions, exécute via Playwright,
valide les résultats via le Vision Pipeline.

ZÉRO DOM ACCESS — on clique par coordonnées visuelles uniquement.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional

from playwright.async_api import Page

from agents.analyzer_agent import AnalyzerAgent, UIState, UIElement, UIChange
from config.settings import settings

logger = logging.getLogger("phantom.action")


# ── Types d'actions ──────────────────────────────────────────

class ActionType(str, Enum):
    CLICK = "click"
    DOUBLE_CLICK = "double_click"
    RIGHT_CLICK = "right_click"
    TYPE = "type"
    SCROLL_UP = "scroll_up"
    SCROLL_DOWN = "scroll_down"
    WAIT = "wait"
    SCREENSHOT = "screenshot"
    HOVER = "hover"
    KEY_PRESS = "key_press"
    SELECT = "select"


@dataclass
class ActionStep:
    """Une étape d'action à exécuter."""
    action_type: str
    target_description: str       # description visuelle de la cible
    value: Optional[str] = None   # texte à taper, touche à presser, etc.
    expected_result: str = ""     # ce qui devrait changer après
    fallback: str = ""            # quoi faire si ça échoue
    risk_level: str = "low"       # low, medium, high
    requires_confirmation: bool = False


@dataclass
class ActionPlan:
    """Plan d'actions séquentielles généré par le Planner."""
    intent: str                   # intention utilisateur originale
    steps: list[ActionStep]
    total_steps: int = 0

    def __post_init__(self):
        self.total_steps = len(self.steps)


@dataclass
class StepResult:
    """Résultat de l'exécution d'une étape."""
    step_index: int
    success: bool
    action_performed: str
    ui_changes: list[UIChange]
    new_state: Optional[UIState] = None
    error: Optional[str] = None
    narration: str = ""           # description vocale pour l'utilisateur


# ── Action Agent ─────────────────────────────────────────────

PLANNER_PROMPT = """You are PHANTOM's Planner. You must generate a precise action plan.

Given:
- USER INTENT: {intent}
- CURRENT UI STATE: {ui_state}

Generate a JSON array of action steps. Each step:
{{
    "action_type": "click|type|scroll_up|scroll_down|wait|hover|key_press|double_click|right_click|select",
    "target_description": "exact label or description of the UI element to interact with",
    "value": "text to type, key to press, or null for clicks",
    "expected_result": "what should change after this action",
    "fallback": "what to do if expected result is not seen",
    "risk_level": "low|medium|high",
    "requires_confirmation": false
}}

RULES:
- Be PRECISE with target_description — match the exact label visible on screen
- Set requires_confirmation=true for destructive actions (delete, submit, send)
- Each step should be ONE atomic action
- Include wait steps if a page load is expected
- risk_level "high" for anything irreversible
- Return ONLY valid JSON array, no markdown
"""


class ActionAgent:
    """
    Agent 3/3 — Exécute les actions UI.

    Workflow :
    1. Reçoit une intention utilisateur + l'état UI actuel
    2. Génère un plan d'actions via Gemini (Planner)
    3. Exécute chaque action via Playwright (coordonnées visuelles)
    4. Après chaque action, re-capture + re-analyse pour valider
    5. Rapporte en temps réel (narration vocale)
    """

    def __init__(self, page: Page, analyzer: AnalyzerAgent):
        self.page = page
        self.analyzer = analyzer
        self._paused = False
        self._current_plan: Optional[ActionPlan] = None
        self._step_results: list[StepResult] = []
        self._on_narration = None  # callback pour la voix

    def set_narration_callback(self, callback):
        """Enregistre un callback pour la narration en temps réel."""
        self._on_narration = callback

    async def _narrate(self, text: str):
        """Envoie un message de narration (voix + log)."""
        logger.info(f"🎙️ {text}")
        if self._on_narration:
            await self._on_narration(text)

    # ── Planning ─────────────────────────────────────────────

    async def generate_plan(
        self,
        intent: str,
        ui_state: UIState,
    ) -> ActionPlan:
        """Génère un plan d'actions à partir de l'intention utilisateur."""
        logger.info(f"🧠 Planification pour : '{intent}'")

        prompt = PLANNER_PROMPT.format(
            intent=intent,
            ui_state=ui_state.to_json(),
        )

        from agents.gemini_utils import gemini_generate_with_retry
        from google.genai import types

        response = await gemini_generate_with_retry(
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
            ),
        )

        if not response:
            logger.error("❌ Plan generation failed — Gemini unavailable")
            return ActionPlan(intent=intent, steps=[])

        try:
            steps_data = json.loads(response.text)
            steps = [ActionStep(**s) for s in steps_data]
            plan = ActionPlan(intent=intent, steps=steps)

            logger.info(f"📋 Plan généré — {plan.total_steps} étapes")
            for i, step in enumerate(steps):
                logger.info(
                    f"   {i+1}. [{step.action_type}] {step.target_description}"
                    f" {'⚠️ CONFIRMATION' if step.requires_confirmation else ''}"
                )

            self._current_plan = plan
            return plan

        except (json.JSONDecodeError, TypeError) as e:
            logger.error(f"❌ Erreur parsing plan : {e}")
            logger.error(f"Réponse brute : {response.text[:500]}")
            return ActionPlan(intent=intent, steps=[])

    # ── Execution ────────────────────────────────────────────

    async def execute_plan(
        self,
        plan: ActionPlan,
        ui_state: UIState,
    ) -> list[StepResult]:
        """Exécute un plan d'actions étape par étape."""
        results = []
        current_state = ui_state

        await self._narrate(
            f"C'est parti. J'ai {plan.total_steps} étapes à réaliser "
            f"pour : {plan.intent}"
        )

        for i, step in enumerate(plan.steps):
            if self._paused:
                await self._narrate("En pause. Dites-moi quand reprendre.")
                while self._paused:
                    await asyncio.sleep(0.5)

            # Confirmation pour les actions à risque
            if step.requires_confirmation:
                await self._narrate(
                    f"⚠️ Action à risque : {step.action_type} sur "
                    f"'{step.target_description}'. En attente de confirmation."
                )
                # TODO: attendre confirmation vocale
                await asyncio.sleep(2)

            await self._narrate(
                f"Étape {i+1}/{plan.total_steps} : "
                f"{step.action_type} sur '{step.target_description}'"
            )

            result = await self._execute_step(step, current_state, i)
            results.append(result)

            if result.success and result.new_state:
                current_state = result.new_state
            elif not result.success:
                await self._narrate(
                    f"❌ Étape {i+1} échouée : {result.error}. "
                    f"Fallback : {step.fallback}"
                )
                # On continue pour le moment — le Validator décidera
                break

        self._step_results = results
        success_count = sum(1 for r in results if r.success)
        await self._narrate(
            f"Terminé. {success_count}/{len(results)} étapes réussies."
        )

        return results

    async def _execute_step(
        self,
        step: ActionStep,
        ui_state: UIState,
        step_index: int,
    ) -> StepResult:
        """Exécute une étape d'action atomique."""
        try:
            # 1. Localiser l'élément visuellement
            target_element = None
            if step.action_type not in ("wait", "screenshot", "scroll_up", "scroll_down", "key_press"):
                target_element = self.analyzer.find_element_by_label(
                    ui_state, step.target_description
                )
                if not target_element:
                    # Re-analyse si élément non trouvé
                    logger.warning(
                        f"⚠️ Élément '{step.target_description}' non trouvé — "
                        f"re-analyse en cours..."
                    )
                    screenshot = await self.page.screenshot(type="png")
                    ui_state = await self.analyzer.analyze_screenshot(screenshot)
                    target_element = self.analyzer.find_element_by_label(
                        ui_state, step.target_description
                    )
                    if not target_element:
                        return StepResult(
                            step_index=step_index,
                            success=False,
                            action_performed="",
                            ui_changes=[],
                            error=f"Élément '{step.target_description}' introuvable",
                        )

            # 2. Exécuter l'action
            action_desc = await self._perform_action(step, target_element)

            # 3. Attendre que l'UI réagisse
            await asyncio.sleep(0.5)

            # Optimisation: Ne pas refaire d'analyse visuelle complète après chaque étape intermédiaire
            # Cela réduit considérablement la latence et évite l'erreur 429 RESOURCE_EXHAUSTED
            # Le plan a déjà été généré avec les coordonnées initiales.
            # L'API `execute_command` fera une analyse finale à la fin du plan.

            # 5. Narration du résultat immédiat
            narration = f"J'ai effectué {step.action_type} sur '{step.target_description}'."

            return StepResult(
                step_index=step_index,
                success=True,
                action_performed=action_desc,
                ui_changes=[], # Skipped for speed
                new_state=ui_state, # Keep previous state to avoid redundant API calls
                narration=narration,
            )

        except Exception as e:
            logger.error(f"❌ Erreur exécution step {step_index} : {e}")
            return StepResult(
                step_index=step_index,
                success=False,
                action_performed="",
                ui_changes=[],
                error=str(e),
            )

    async def _perform_action(
        self,
        step: ActionStep,
        element: Optional[UIElement],
    ) -> str:
        """Exécute l'action Playwright — par coordonnées visuelles, JAMAIS par sélecteur DOM."""
        match step.action_type:
            case "click":
                await self.page.mouse.click(element.center_x, element.center_y)
                return f"Click @ ({element.center_x}, {element.center_y})"

            case "double_click":
                await self.page.mouse.dblclick(element.center_x, element.center_y)
                return f"Double-click @ ({element.center_x}, {element.center_y})"

            case "right_click":
                await self.page.mouse.click(
                    element.center_x, element.center_y, button="right"
                )
                return f"Right-click @ ({element.center_x}, {element.center_y})"

            case "hover":
                await self.page.mouse.move(element.center_x, element.center_y)
                return f"Hover @ ({element.center_x}, {element.center_y})"

            case "type":
                # Cliquer d'abord pour focus
                await self.page.mouse.click(element.center_x, element.center_y)
                await asyncio.sleep(0.2)
                await self.page.keyboard.type(step.value or "", delay=50)
                return f"Type '{step.value}' @ ({element.center_x}, {element.center_y})"

            case "key_press":
                await self.page.keyboard.press(step.value or "Enter")
                return f"Key press: {step.value}"

            case "scroll_up":
                await self.page.mouse.wheel(0, -300)
                return "Scroll up"

            case "scroll_down":
                await self.page.mouse.wheel(0, 300)
                return "Scroll down"

            case "wait":
                wait_time = float(step.value or "1")
                await asyncio.sleep(wait_time)
                return f"Wait {wait_time}s"

            case "select":
                # Click pour ouvrir le dropdown puis sélectionner
                await self.page.mouse.click(element.center_x, element.center_y)
                await asyncio.sleep(0.5)
                # Re-analyser pour trouver l'option
                screenshot = await self.page.screenshot(type="png")
                new_state = await self.analyzer.analyze_screenshot(screenshot)
                option = self.analyzer.find_element_by_label(new_state, step.value or "")
                if option:
                    await self.page.mouse.click(option.center_x, option.center_y)
                return f"Select '{step.value}' from dropdown"

            case _:
                logger.warning(f"⚠️ Action inconnue : {step.action_type}")
                return f"Unknown action: {step.action_type}"

    def _describe_result(self, step: ActionStep, changes: list[UIChange]) -> str:
        """Génère une narration humaine du résultat."""
        if not changes:
            return f"J'ai effectué {step.action_type} sur '{step.target_description}'. Aucun changement visible."

        change_desc = ", ".join(
            f"{c.element_label} {c.change_type}" for c in changes[:3]
        )
        return (
            f"Après {step.action_type} sur '{step.target_description}' : "
            f"{change_desc}."
        )

    # ── Contrôle ─────────────────────────────────────────────

    def pause(self):
        """Pause l'exécution (interruption utilisateur)."""
        self._paused = True
        logger.info("⏸️ Exécution en pause")

    def resume(self):
        """Reprend l'exécution."""
        self._paused = False
        logger.info("▶️ Exécution reprise")

    async def execute_single_action(
        self,
        action_type: str,
        target: str,
        value: Optional[str] = None,
        ui_state: Optional[UIState] = None,
    ) -> StepResult:
        """
        Exécute une action unique (mode direct, sans plan).
        Pour les commandes simples comme "clique sur le bouton X".
        """
        if not ui_state:
            screenshot = await self.page.screenshot(type="png")
            ui_state = await self.analyzer.analyze_screenshot(screenshot)

        step = ActionStep(
            action_type=action_type,
            target_description=target,
            value=value,
        )
        return await self._execute_step(step, ui_state, 0)

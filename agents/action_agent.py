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
import sys
from dataclasses import dataclass, asdict
from enum import Enum
from typing import Optional

from playwright.async_api import Page

from agents.analyzer_agent import AnalyzerAgent, UIState, UIElement, UIChange
from agents.mcp_client import ChromeMCPClient
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

PLANNER_PROMPT = """You are PHANTOM's Action Planner. Analyze the current UI and generate a precise action plan.

USER INTENT: {intent}
CURRENT UI STATE: {ui_state}

Generate a JSON array of action steps. Each step:
{{
    "action_type": "click|type|scroll_up|scroll_down|wait|hover|key_press|double_click|right_click|select",
    "target_description": "EXACT text label of the UI element as it appears on screen",
    "value": "text to type, key name, or null",
    "expected_result": "what should change after this action",
    "fallback": "what to try if this fails",
    "risk_level": "low|medium|high",
    "requires_confirmation": false
}}

CRITICAL RULES FOR REAL SITES:

1. GOOGLE FLIGHTS — Flight search form:
   - Click the "Where from?" field (or "Origin" input)
   - Wait 0.5s for it to focus  
   - Type the departure city/airport (e.g. "Paris CDG")
   - Wait 1s for autocomplete dropdown to appear
   - Press "ArrowDown" then "Enter" to select the first suggestion OR click the first suggestion
   - Then click "Where to?" field
   - Type destination city (e.g. "Dubai")
   - Wait 1s, then ArrowDown + Enter to select suggestion
   - Click the Departure date field
   - Type or click the date (format: e.g. "March 14")
   - Click "Search" button

2. GOOGLE MAPS — Directions:
   - Click the "Directions" button first
   - Wait 1s
   - Click "Choose starting point" or the first route input  
   - Type the origin
   - Press Enter
   - Click "Choose destination" or second route input
   - Type the destination
   - Press Enter

3. SEARCH FIELDS (Google, news sites):
   - Click the search box
   - Type the query
   - Press Enter to submit

4. ARTICLE/NEWS SITES:
   - After search results appear, click on the most relevant result link
   - Wait 2s for the article to load

5. GENERAL RULES:
   - After ANY click that opens a new panel/page/dialog: add wait step with value "2"
   - target_description MUST match the exact visible text in the UI state provided
   - For text fields: click first to focus, then type
   - For autocomplete: always add ArrowDown + Enter after typing to confirm the suggestion
   - Each step = ONE atomic action
   - Set requires_confirmation=true only for destructive actions (payment, delete, send)

Return ONLY a valid JSON array, no markdown, no explanation.
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

    def __init__(self, page: Page, analyzer: AnalyzerAgent, mcp_client: Optional[ChromeMCPClient] = None):
        self.page = page
        self.analyzer = analyzer
        self.mcp_client = mcp_client
        self._paused = False
        self._current_plan: Optional[ActionPlan] = None
        self._step_results: list[StepResult] = []
        self._on_narration = None  # callback pour la voix

    def set_narration_callback(self, callback):
        """Enregistre un callback pour la narration en temps réel."""
        self._on_narration = callback

    def set_action_callback(self, callback):
        """Enregistre un callback pour diffuser les actions en cours."""
        self._on_action = callback

    async def _narrate(self, text: str):
        """Envoie un message de narration (voix + log)."""
        logger.info(f"🎙️ {text}")
        if self._on_narration:
            await self._on_narration(text)
            
    async def _broadcast_action(self, text: str):
        """Envoie un message de status d'action exécutée."""
        logger.info(f"⚙️ {text}")
        if hasattr(self, '_on_action') and self._on_action:
            await self._on_action(text)

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

        tools_config = None
        if self.mcp_client:
            try:
                mcp_tools = await self.mcp_client.get_available_tools()
                function_declarations = []
                for t in mcp_tools:
                    # Map MCP tool schema → Gemini function declaration
                    func = types.FunctionDeclaration(
                        name=t.name,
                        description=getattr(t, "description", "") or "",
                        parameters=getattr(t, "inputSchema", {}) or {},
                    )
                    function_declarations.append(func)

                if function_declarations:
                    tools_config = [{"function_declarations": function_declarations}]
                    logger.info(
                        f"🧩 Exposing {len(function_declarations)} Chrome DevTools MCP "
                        f"tools to Gemini planner."
                    )
            except Exception as e:
                logger.warning(f"⚠️ Unable to load MCP tools, continuing without them: {e}")

        response = await gemini_generate_with_retry(
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.2,
                tools=tools_config,
            ),
        )

        if not response:
            logger.error("❌ Plan generation failed — Gemini unavailable")
            return ActionPlan(intent=intent, steps=[])

        # If Gemini chose to call a DevTools MCP function instead of returning a plan,
        # execute the tool and feed the result back into a second planning pass.
        def _extract_function_call(resp: object):
            try:
                for cand in getattr(resp, "candidates", []) or []:
                    content = getattr(cand, "content", None)
                    parts = getattr(content, "parts", None) or []
                    for part in parts:
                        fc = getattr(part, "function_call", None)
                        if fc:
                            return fc
            except Exception:
                return None
            return None

        function_call = _extract_function_call(response) if self.mcp_client else None

        if function_call and self.mcp_client:
            try:
                tool_name = getattr(function_call, "name", "")
                tool_args = getattr(function_call, "args", {}) or {}
                logger.info(f"🔧 Gemini requested DevTools tool call: {tool_name}")
                mcp_result = await self.mcp_client.call_tool(tool_name, tool_args)

                # Second pass: provide DevTools result as additional context and
                # force Gemini to output the final JSON ActionStep array.
                followup_prompt = PLANNER_PROMPT.format(
                    intent=intent,
                    ui_state=ui_state.to_json(),
                ) + (
                    "\n\nYou also have access to Chrome DevTools data.\n"
                    f"You previously called the DevTools tool `{tool_name}` with arguments:\n"
                    f"{json.dumps(tool_args, indent=2, ensure_ascii=False)}\n\n"
                    "Here is the JSON result returned by that tool:\n"
                    f"{json.dumps(mcp_result, indent=2, ensure_ascii=False, default=str)}\n\n"
                    "Using EVERYTHING above (UIState + DevTools result), generate the final "
                    "JSON array of action steps exactly in the format specified earlier.\n"
                    "Return ONLY a valid JSON array, no markdown, no comments, no extra keys."
                )

                response = await gemini_generate_with_retry(
                    contents=[followup_prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0.2,
                    ),
                )
                if not response:
                    logger.error("❌ Second-pass plan generation failed after MCP tool call")
                    return ActionPlan(intent=intent, steps=[])
            except Exception as e:
                logger.error(f"❌ Error handling MCP function call: {e}")
                # Fall back to trying to parse whatever JSON we have (if any)

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

        # No opening narration here — _handle_ws_command already said it

        for i, step in enumerate(plan.steps):
            if self._paused:
                await self._narrate("I'm paused. Let me know when to continue.")
                while self._paused:
                    await asyncio.sleep(0.5)

            # Confirmation pour les actions à risque
            if step.requires_confirmation:
                await self._narrate(
                    f"⚠️ This action could be risky: {step.action_type} on "
                    f"'{step.target_description}'. Waiting for your confirmation."
                )
                await asyncio.sleep(2)

            # Broadcast the action being attempted
            action_msg = f"Executing: {step.action_type}"
            if step.target_description:
                action_msg += f" on '{step.target_description}'"
            if step.value:
                action_msg += f" with value '{step.value}'"
            await self._broadcast_action(action_msg)

            # Execution
            result = await self._execute_step(step, current_state, i)
            results.append(result)

            if result.success and result.new_state:
                current_state = result.new_state
            elif not result.success:
                # Only narrate failures — successes are silent
                logger.warning(
                    f"⚠️ Step {i+1} failed: {step.target_description} — {result.error}"
                )
                break

        self._step_results = results
        # No completion narration — _summarize_results will speak for us

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
                    # Re-analyse si élément non trouvé — attend 2s pour
                    # permettre aux pages dynamiques (ex: Google Maps)
                    # de charger leurs nouveaux éléments UI
                    logger.warning(
                        f"⚠️ Élément '{step.target_description}' non trouvé — "
                        f"attente 2s + re-analyse en cours..."
                    )
                    await asyncio.sleep(2.0)
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
            # Clicks qui changent la page → attente longue + re-scan
            # Type/scroll/key_press → attente courte, pas de re-scan
            is_page_changing = step.action_type in ("click", "double_click", "select")

            if is_page_changing:
                # Les clics ouvrent souvent de nouveaux panneaux/pages
                # Il faut attendre que le contenu se charge avant la prochaine étape
                await asyncio.sleep(2.0)
                screenshot = await self.page.screenshot(type="png")
                new_state = await self.analyzer.analyze_screenshot(screenshot)
                
                # Broadcaster le nouveau screenshot
                import base64
                b64_img = base64.b64encode(screenshot).decode("utf-8")
                # Need to use the global broadcast if possible, or just skip it since main.py will see results
                
                narration = f"J'ai cliqué sur '{step.target_description}'. {len(new_state.elements)} éléments détectés."
                return StepResult(
                    step_index=step_index,
                    success=True,
                    action_performed=action_desc,
                    ui_changes=[],
                    new_state=new_state,
                    narration=narration,
                )
            else:
                # Type, scroll, key_press → pas besoin de re-scanner
                await asyncio.sleep(0.5)  # Légèrement allongé pour la fluidité
                narration = f"J'ai effectué {step.action_type} sur '{step.target_description}'."
                return StepResult(
                    step_index=step_index,
                    success=True,
                    action_performed=action_desc,
                    ui_changes=[],
                    new_state=ui_state,
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
                await asyncio.sleep(0.3)
                # Triple-click to select any existing text in the field
                await self.page.mouse.click(element.center_x, element.center_y, click_count=3)
                await asyncio.sleep(0.2)
                # Select all as fallback (Ctrl+A / Cmd+A) then delete
                modifier = "Meta" if sys.platform == "darwin" else "Control"
                await self.page.keyboard.press(f"{modifier}+a")
                await asyncio.sleep(0.1)
                await self.page.keyboard.press("Backspace")
                await asyncio.sleep(0.3)
                # Now type the new value into the cleared field
                await self.page.keyboard.type(step.value or "", delay=50)
                await asyncio.sleep(0.5)
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

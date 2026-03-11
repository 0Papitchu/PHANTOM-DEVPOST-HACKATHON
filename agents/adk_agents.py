# phantom-ui-navigator/agents/adk_agents.py
"""
ADK Integration — Google Agent Development Kit wrappers.
Wraps Phantom's custom agents with ADK's LlmAgent for proper
framework integration and orchestration.

@module adk_agents
@description ADK LlmAgent wrappers for Phantom's Vision and Planner agents
@author ANTIGRAVITY
@created 2026-03-06
@dependencies google-adk, google-genai
@used-by api/main.py
"""

import logging
from typing import Optional

from google.adk.agents import LlmAgent
from google.genai import types

from config.settings import settings

logger = logging.getLogger("phantom.adk")


# ── ADK Agent Definitions ────────────────────────────────────

# Vision Analyzer Agent — wraps our Gemini Vision pipeline
vision_analyzer_agent = LlmAgent(
    name="PhantomVisionAnalyzer",
    model=settings.gemini_model,
    instruction="""You are PHANTOM's Vision System. You analyze screenshots of any application
and return structured JSON describing all interactive UI elements visible on screen.

You identify buttons, inputs, links, dropdowns, menus, checkboxes, icons, and any
other clickable or interactive elements. You provide precise bounding box coordinates
in pixels from the top-left corner.

You NEVER access the DOM or source code. You see ONLY what a human would see on screen.
Your analysis enables a downstream Action Agent to interact with the UI via coordinates.""",
    generate_content_config=types.GenerateContentConfig(
        temperature=0.1,
        response_mime_type="application/json",
    ),
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)


# Action Planner Agent — wraps our plan generation
action_planner_agent = LlmAgent(
    name="PhantomActionPlanner",
    model=settings.gemini_model,
    instruction="""You are PHANTOM's Action Planner. Given a user intent and the current UI state
(as a structured JSON of detected elements), you generate a precise sequence of
atomic actions to fulfill the user's request.

Each action step specifies exactly which UI element to interact with and how
(click, type, scroll, hover, key_press, etc.). You plan defensively — include
wait steps after page loads, set risk_level=high for irreversible actions, and
provide fallback strategies.

You ONLY plan interactions via visual coordinates — no DOM selectors, no APIs.""",
    generate_content_config=types.GenerateContentConfig(
        temperature=0.2,
        response_mime_type="application/json",
    ),
    disallow_transfer_to_parent=True,
    disallow_transfer_to_peers=True,
)


# Root Orchestrator Agent — coordinates Vision + Planner
phantom_orchestrator = LlmAgent(
    name="PhantomOrchestrator",
    model=settings.gemini_model,
    instruction="""You are PHANTOM, an AI agent that navigates any user interface
by vision and voice. You coordinate two sub-agents:

1. PhantomVisionAnalyzer — analyzes screenshots to understand UI state
2. PhantomActionPlanner — generates action plans from user intent

Your mission: help users interact with ANY application (legacy enterprise software,
web apps, desktop apps) without needing access to the source code, DOM, or APIs.
You see what the user sees, and you act like a human would — by clicking on visual
coordinates.

You also serve users with accessibility needs by narrating your actions in real-time.""",
    sub_agents=[vision_analyzer_agent, action_planner_agent],
)


def get_adk_agents() -> dict:
    """
    Returns the ADK agent hierarchy for initialization logging.
    The actual execution still uses our custom pipeline (ScreenshotAgent →
    AnalyzerAgent → ActionAgent) for Playwright integration, but ADK
    provides the framework backbone for orchestration metadata.
    """
    logger.info("🤖 ADK Agents initialized:")
    logger.info(f"   ├── {phantom_orchestrator.name} (root)")
    logger.info(f"   ├── {vision_analyzer_agent.name} (vision)")
    logger.info(f"   └── {action_planner_agent.name} (planner)")
    return {
        "orchestrator": phantom_orchestrator,
        "vision": vision_analyzer_agent,
        "planner": action_planner_agent,
    }

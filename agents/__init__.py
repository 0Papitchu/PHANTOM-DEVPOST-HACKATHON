# phantom-ui-navigator/agents/__init__.py
"""
Phantom Agents Package
3 agents coordonnés : Screenshot → Analyzer → Action
"""

from agents.screenshot_agent import ScreenshotAgent
from agents.analyzer_agent import AnalyzerAgent
from agents.action_agent import ActionAgent

__all__ = ["ScreenshotAgent", "AnalyzerAgent", "ActionAgent"]

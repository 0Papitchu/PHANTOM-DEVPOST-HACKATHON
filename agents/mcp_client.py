"""
Chrome DevTools MCP Client — Bridge between Phantom and Chrome DevTools MCP.

@module mcp_client
@description Asynchronous client for talking to the Chrome DevTools MCP server
             over stdio, exposing DevTools tools to Gemini as callable functions.
@author GPT-AGENT
@created 2026-03-15
@dependencies mcp, node/npm (for npx + @puppeteer/chrome-devtools-mcp)
@used-by agents/action_agent.py, api/main.py
"""

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger("phantom.mcp")


class ChromeMCPClient:
    """
    Lightweight helper around the Chrome DevTools MCP server.

    Design notes:
    - We spawn the MCP server via `npx @puppeteer/chrome-devtools-mcp` on demand.
    - Each call establishes a fresh stdio session; this avoids long-lived subprocess
      management inside FastAPI and keeps failure modes simple for the hackathon.
    - The ActionAgent continues to act via coordinates only; DevTools is an optional
    - introspection layer Gemini can tap into when its vision context is insufficient.
    """

    def __init__(
        self,
        browser_url: str = "http://127.0.0.1:9222",
        npx_command: str = "npx",
        package_spec: str = "@puppeteer/chrome-devtools-mcp@latest",
    ) -> None:
        self.browser_url = browser_url
        self.npx_command = npx_command
        self.package_spec = package_spec

    def _server_params(self) -> StdioServerParameters:
        """Build stdio server parameters for the Chrome DevTools MCP server."""
        return StdioServerParameters(
            command=self.npx_command,
            args=[
                "-y",
                self.package_spec,
                f"--browser-url={self.browser_url}",
            ],
        )

    async def get_available_tools(self) -> List[Any]:
        """
        Return the list of tools exposed by the Chrome DevTools MCP server.

        The return value is the raw list of Tool objects from the MCP SDK; callers
        can inspect `.name`, `.description`, and `.inputSchema` to build LLM tools.
        """
        server_params = self._server_params()
        logger.info("🔌 Connecting to Chrome DevTools MCP server to list tools...")

        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools_response = await session.list_tools()

                tools = getattr(tools_response, "tools", tools_response)
                logger.info(f"🧰 Chrome DevTools MCP tools available: {[t.name for t in tools]}")
                return list(tools)

    async def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Call a specific MCP tool by name with JSON-serializable arguments.

        Returns a simplified dict including:
        - tool: tool name
        - arguments: arguments sent
        - is_error: whether the tool reported an error
        - structured_content: machine-readable JSON result when available
        - content: human-readable content blocks (text only, best-effort)
        """
        server_params = self._server_params()
        args = arguments or {}

        logger.info(f"🛠️ Calling Chrome DevTools MCP tool '{name}' with args={args}")

        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, args)

                is_error = getattr(result, "is_error", False)
                structured = getattr(result, "structured_content", None)
                content_blocks = []

                for block in getattr(result, "content", []) or []:
                    # Best-effort extraction of text content for logging / LLM context.
                    text = getattr(block, "text", None) or getattr(block, "value", None)
                    if text:
                        content_blocks.append(str(text))

                payload: Dict[str, Any] = {
                    "tool": name,
                    "arguments": args,
                    "is_error": is_error,
                    "structured_content": structured,
                    "content": content_blocks,
                }

                # Keep logs concise but structured for debugging
                try:
                    pretty = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
                    logger.info(f"📡 MCP tool result for '{name}': {pretty}")
                except Exception:
                    logger.info(f"📡 MCP tool result for '{name}' (non-JSON-serializable)")

                return payload


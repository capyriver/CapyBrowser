#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2.0,<2"]
# ///
"""Minimal browsing agent: a local Ollama model + CapyBrowser over MCP.

    ollama pull qwen3:4b-instruct
    uv run examples/ollama_agent.py "Open https://news.ycombinator.com and tell me the top story"

Environment:
    OLLAMA_MODEL      model name (default: qwen3:4b-instruct)
    OLLAMA_HOST       Ollama URL (default: http://127.0.0.1:11434)
    CAPYBROWSER_CMD   command that starts the MCP server
                      (default: uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser)
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import sys
import urllib.request

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:4b-instruct")
HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
CMD = shlex.split(os.environ.get("CAPYBROWSER_CMD",
                                 "uvx --from git+https://github.com/capyriver/CapyBrowser capybrowser"))
MAX_STEPS = 15
SYSTEM = (
    "You control a real web browser through tools. Use the tools to do the task; do not guess. "
    "Page views list clickable elements as [id]; use those ids with click and type_text. "
    "Ids change after every action, so always use ids from the latest result. "
    "Perform every step the user asks with tool calls, in order, before answering. "
    "When the task is done, reply with a short final answer and no tool call."
)

# Ollama is local: never send its traffic through an HTTP(S) proxy.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def chat(messages: list[dict], tools: list[dict]) -> dict:
    body = json.dumps({"model": MODEL, "messages": messages, "tools": tools, "stream": False, "think": False,
                       "options": {"num_ctx": 16384, "temperature": 0.2}}).encode()
    req = urllib.request.Request(f"{HOST}/api/chat", data=body, headers={"Content-Type": "application/json"})
    with _OPENER.open(req, timeout=900) as r:
        return json.loads(r.read())["message"]


async def run(task: str) -> str:
    params = StdioServerParameters(command=CMD[0], args=CMD[1:], env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = [{"type": "function",
                      "function": {"name": t.name, "description": t.description or "", "parameters": t.inputSchema}}
                     for t in (await session.list_tools()).tools]
            messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task}]
            for _ in range(MAX_STEPS):
                # keep the context small: only the two latest page views are kept in full
                tool_msgs = [m for m in messages if m["role"] == "tool"]
                for m in tool_msgs[:-2]:
                    if len(m["content"]) > 400:
                        m["content"] = m["content"][:400] + "\n[...older page view truncated...]"
                msg = await asyncio.to_thread(chat, messages, tools)
                calls = msg.get("tool_calls") or []
                messages.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": calls})
                if not calls:
                    return msg.get("content", "")
                for call in calls:
                    name = call["function"]["name"]
                    args = call["function"].get("arguments") or {}
                    if isinstance(args, str):
                        args = json.loads(args or "{}")
                    print(f"-> {name}({json.dumps(args, ensure_ascii=False)})", file=sys.stderr)
                    result = await session.call_tool(name, args)
                    text = "".join(getattr(c, "text", "") for c in result.content)
                    messages.append({"role": "tool", "content": text, "tool_name": name})
            return "(stopped: step limit reached)"


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('usage: uv run examples/ollama_agent.py "your task"')
    print(asyncio.run(run(" ".join(sys.argv[1:]))))

"""Small MCP stdio client used by the generic LLM loop."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from typing import Any


class MCPClient:
    def __init__(self) -> None:
        self.process: subprocess.Popen[str] | None = None
        self.next_id = 1
        self.tools: list[dict[str, Any]] = []

    def start(self) -> None:
        command = shlex.split(os.getenv("MWS_MCP_COMMAND", "")) or [sys.executable, "-m", "mws_mcp.server"]
        env = os.environ.copy(); source = env.get("MTS_AGENT_DIR", "").strip()
        if source: env["PYTHONPATH"] = source + os.pathsep + env.get("PYTHONPATH", "")
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1, env=env)
        self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "vibecoding-agent", "version": "0.1.0"}})
        self.notify("notifications/initialized", {})
        self.tools = self.request("tools/list", {}).get("tools", [])

    def stop(self) -> None:
        if not self.process: return
        if self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=2)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(timeout=2)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream: stream.close()
        self.process = None

    def notify(self, method: str, params: dict[str, Any]) -> None:
        if not self.process or not self.process.stdin: raise RuntimeError("MCP server is not running")
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params,}, ensure_ascii=True) + "\n"); self.process.stdin.flush()

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.process or not self.process.stdin or not self.process.stdout: raise RuntimeError("MCP server is not running")
        request_id = self.next_id; self.next_id += 1
        self.process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}, ensure_ascii=True) + "\n"); self.process.stdin.flush()
        raw = self.process.stdout.readline()
        if not raw:
            raise RuntimeError("MCP server exited before responding")
        response = json.loads(raw)
        if "error" in response: raise RuntimeError(f"MCP {method}: {response['error']['message']}")
        return response.get("result") or {}

    def configure(self, context: dict[str, Any]) -> dict[str, Any]:
        return self.request("vibe/configure", context)

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        content = result.get("content") or []
        text = content[0].get("text", "{}") if content and isinstance(content[0], dict) else "{}"
        return json.loads(text)

    def openai_tools(self) -> list[dict[str, Any]]:
        return [{"type": "function", "function": {"name": tool["name"], "description": tool.get("description", ""), "parameters": tool.get("inputSchema", {"type": "object", "properties": {}})}} for tool in self.tools if not tool.get("annotations", {}).get("x-vibe-internal")]

    def context_tool(self) -> str:
        for tool in self.tools:
            if tool.get("annotations", {}).get("x-vibe-role") == "context": return str(tool["name"])
        raise RuntimeError("Selected MCP server exposes no context tool")

    def tool_role(self, name: str) -> str | None:
        for tool in self.tools:
            if tool.get("name") == name:
                role = tool.get("annotations", {}).get("x-vibe-role")
                return str(role) if role else None
        return None

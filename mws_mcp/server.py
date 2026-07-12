"""A minimal JSON-RPC stdio MCP server; no third-party runtime is required."""
from __future__ import annotations

import json
import sys
from typing import Any

from .runtime import PlatformRuntime


RUNTIME = PlatformRuntime()
TOOLS = [
    {"name": "platform_contract", "description": "Return the selected platform skill contract.", "inputSchema": {"type": "object", "properties": {}}, "annotations": {"x-vibe-role": "context", "readOnlyHint": True}},
    {"name": "inspect_existing_bot", "description": "Read the configured existing bot before an update.", "inputSchema": {"type": "object", "properties": {}}, "annotations": {"readOnlyHint": True}},
    {"name": "save_draft", "description": "Save and validate a draft bot attributes object.", "inputSchema": {"type": "object", "properties": {"bot": {"type": "object"}}, "required": ["bot"]}},
    {"name": "validate_draft", "description": "Validate the saved draft.", "inputSchema": {"type": "object", "properties": {}}, "annotations": {"readOnlyHint": True}},
    {"name": "publish_draft", "description": "Import and publish the saved valid draft; honours dry-run.", "inputSchema": {"type": "object", "properties": {}}, "annotations": {"x-vibe-role": "publication"}},
    {"name": "test_published_bot", "description": "Send one independent engine message; optional assertions are evaluated against the visible reply, suggestions, and commands.", "inputSchema": {"type": "object", "properties": {"message": {"type": "string"}, "expectContains": {"type": "array", "items": {"type": "string"}}, "expectButtons": {"type": "array", "items": {"type": "string"}}, "expectCommand": {"type": "string"}}}},
    {"name": "verify_published_bot", "description": "Run a model-defined independent black-box test suite against the published bot. Use this to prove the requested paths work before completion.", "inputSchema": {"type": "object", "properties": {"tests": {"type": "array", "minItems": 1, "items": {"type": "object", "properties": {"message": {"type": "string"}, "expectContains": {"type": "array", "items": {"type": "string"}}, "expectButtons": {"type": "array", "items": {"type": "string"}}, "expectCommand": {"type": "string"}}, "required": ["message"]}}}, "required": ["tests"]}, "annotations": {"x-vibe-role": "verification"}},
]


def result(value: Any) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=True)}]}


def call(name: str, args: dict[str, Any]) -> Any:
    if name == "platform_contract": return RUNTIME.contract()
    if name == "inspect_existing_bot": return RUNTIME.inspect()
    if name == "save_draft": return RUNTIME.save_draft(args.get("bot"))
    if name == "validate_draft":
        errors = RUNTIME.validate(RUNTIME.draft); return {"valid": not errors, "errors": errors}
    if name == "publish_draft": return RUNTIME.publish()
    if name == "test_published_bot": return RUNTIME.engine_test(args.get("message"), args.get("expectContains"), args.get("expectButtons"), args.get("expectCommand"))
    if name == "verify_published_bot": return RUNTIME.verify(args.get("tests"))
    raise ValueError(f"Unknown MCP tool: {name}")


def main() -> int:
    for raw in sys.stdin:
        try:
            request = json.loads(raw); method = request.get("method"); params = request.get("params") or {}
            if method == "notifications/initialized": continue
            if method == "initialize": response = {"protocolVersion": "2024-11-05", "serverInfo": {"name": "mws-platform-tools", "version": "0.1.0"}, "capabilities": {"tools": {}}}
            elif method == "tools/list": response = {"tools": TOOLS}
            elif method == "tools/call": response = result(call(str(params.get("name")), params.get("arguments") or {}))
            elif method == "vibe/configure": response = RUNTIME.configure(params)
            else: raise ValueError(f"Unsupported method: {method}")
            if "id" in request: print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": response}, ensure_ascii=True), flush=True)
        except Exception as error:
            if "id" in locals().get("request", {}): print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32000, "message": str(error)}}, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SYSTEM = """You are a careful no-code bot engineer. Work iteratively using tools.
First inspect an existing bot when editing. Create a complete generic MWS bot draft via save_draft,
then validate it, repair any reported problem, publish it only when valid, and test it. Do not invent
platform results. Keep botName lowercase ASCII letters, digits and underscores. Ask a concise question
only when the user's request is genuinely ambiguous. A scenario needs entryEdges, nodes, and answer/
interaction blocks. Never mention benchmark tasks or tailor a bot to a hidden test."""

TOOLS = [
    {"type": "function", "function": {"name": "platform_contract", "description": "Read the concise bot contract and current operation mode.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "inspect_existing_bot", "description": "Read an existing bot before changing it.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "save_draft", "description": "Save a proposed bot attributes object and receive structural validation.", "parameters": {"type": "object", "properties": {"bot": {"type": "object", "description": "Bot attributes, not the API data wrapper."}}, "required": ["bot"]}}},
    {"type": "function", "function": {"name": "validate_draft", "description": "Validate the currently saved draft before publishing.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "publish_draft", "description": "Import a valid draft or update an existing bot. This respects dry-run mode.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "test_published_bot", "description": "Send a small independent message to the published bot.", "parameters": {"type": "object", "properties": {"message": {"type": "string"}}}}},
]


@dataclass
class Config:
    base_url: str
    frontend_url: str
    token: str
    workspace: str
    account: str
    llm_url: str
    llm_key: str
    model: str
    dry_run: bool
    existing_bot_id: str | None
    existing_version_id: str | None
    max_turns: int
    test_message: str
    debug_dir: Path
    history_file: str | None

    @classmethod
    def from_env(cls, args: Any) -> "Config":
        root = Path(__file__).resolve().parents[1]
        return cls(
            base_url=os.getenv("PLATFORM_BASE_URL", os.getenv("MTS_PLATFORM_BASE_URL", "http://5.188.27.251:18080")).rstrip("/"),
            frontend_url=os.getenv("PLATFORM_FRONTEND_URL", os.getenv("MTS_PLATFORM_FRONTEND_URL", "http://5.188.27.251:18080")).rstrip("/"),
            token=os.getenv("MTS_PLATFORM_TOKEN", ""), workspace=os.getenv("MTS_AI_WORKSPACE", "default"), account=os.getenv("MTS_AI_ACCOUNT", "default"),
            llm_url=os.getenv("COTYPE_BASE_URL", os.getenv("MWS_BASE_URL", "")).rstrip("/"),
            llm_key=os.getenv("COTYPE_API_KEY", os.getenv("MWS_API_KEY", "")), model=os.getenv("COTYPE_MODEL", os.getenv("COTYPE_MODEL_NAME", os.getenv("MWS_MODEL_NAME", ""))),
            dry_run=args.dry_run, existing_bot_id=args.existing_bot_id, existing_version_id=args.existing_version_id,
            max_turns=max(1, args.max_turns), test_message=args.test_message, debug_dir=root / "debug", history_file=args.history_file,
        )


class Agent:
    def __init__(self, config: Config):
        self.c = config
        self.draft: dict[str, Any] | None = None
        self.last_response: Any = None
        self.c.debug_dir.mkdir(exist_ok=True)

    def headers(self, llm: bool = False) -> dict[str, str]:
        if llm:
            return {"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {self.c.llm_key}"}
        result = {"Accept": "application/json", "Content-Type": "application/json", "X-Ai-Workspace": self.c.workspace, "request-id": uuid.uuid4().hex}
        if self.c.token and self.c.token != "not-required": result["Authorization"] = f"Bearer {self.c.token}"
        if self.c.account: result["X-Ai-Account"] = self.c.account
        return result

    def request(self, method: str, url: str, body: Any = None, llm: bool = False) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self.headers(llm), method=method)
        try:
            with urllib.request.urlopen(req, timeout=180) as res:
                text = res.read().decode("utf-8", "replace")
                return res.status, json.loads(text) if text else {}
        except urllib.error.HTTPError as err:
            text = err.read().decode("utf-8", "replace")
            try: return err.code, json.loads(text)
            except json.JSONDecodeError: return err.code, {"error": text}

    def validate(self, bot: Any) -> list[str]:
        if not isinstance(bot, dict): return ["draft must be a JSON object"]
        errors: list[str] = []
        name = bot.get("botName")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9_]{3,64}", name): errors.append("botName must be 3-64 lowercase ASCII letters, digits, or underscores")
        if not isinstance(bot.get("requestTtlInSeconds"), int): errors.append("requestTtlInSeconds must be an integer")
        if not isinstance(bot.get("noMatchStubAnswer"), str) or len(bot["noMatchStubAnswer"].strip()) < 4: errors.append("noMatchStubAnswer must contain at least 4 characters")
        if bot.get("needPreprocess") not in {"disabled", "required", "optional"}: errors.append("needPreprocess must be disabled, required, or optional")
        scenarios = bot.get("scenarios")
        if not isinstance(scenarios, list) or not scenarios: return errors + ["at least one scenario is required"]
        ids: set[str] = set()
        for index, scenario in enumerate(scenarios):
            if not isinstance(scenario, dict): errors.append(f"scenario {index} must be an object"); continue
            if not scenario.get("name"): errors.append(f"scenario {index} has no name")
            if not isinstance(scenario.get("entryEdges"), list) or not scenario["entryEdges"]: errors.append(f"scenario {index} needs entryEdges")
            nodes = scenario.get("nodes")
            if not isinstance(nodes, list) or not nodes: errors.append(f"scenario {index} needs nodes"); continue
            for node in nodes:
                if not isinstance(node, dict) or not node.get("id"): errors.append(f"scenario {index} has a node without id"); continue
                node_id = str(node["id"])
                if node_id in ids: errors.append(f"duplicate node id: {node_id}")
                ids.add(node_id)
                if not isinstance(node.get("blocks"), list) or not node["blocks"]: errors.append(f"node {node_id} needs blocks")
        return errors

    def contract(self) -> dict[str, Any]:
        return {"mode": "update" if self.c.existing_bot_id else "create", "dryRun": self.c.dry_run, "attributes": {"required": ["botName", "requestTtlInSeconds", "noMatchStubAnswer", "needPreprocess", "scenarios"], "scenario": "name, entryEdges, nodes; each node has unique id and blocks", "preprocess": ["disabled", "required", "optional"]}, "endpoints": {"create": "/api/v3/nocode/bots/import/", "update": "/api/v3/nocode/bots/{botId}/import-version/"}}

    def inspect(self) -> dict[str, Any]:
        if not self.c.existing_bot_id: return {"note": "No existing bot selected; create a new one."}
        status, data = self.request("GET", f"{self.c.base_url}/api/v3/nocode/bots/{self.c.existing_bot_id}/")
        return {"status": status, "data": data}

    def save_draft(self, bot: Any) -> dict[str, Any]:
        self.draft = bot if isinstance(bot, dict) else None
        errors = self.validate(bot)
        if self.draft:
            (self.c.debug_dir / "last_platform_payload.json").write_text(json.dumps({"data": {"type": "bots", "attributes": self.draft}}, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"saved": bool(self.draft), "valid": not errors, "errors": errors}

    def publish(self) -> dict[str, Any]:
        if not self.draft: return {"published": False, "error": "no draft saved"}
        errors = self.validate(self.draft)
        if errors: return {"published": False, "errors": errors}
        payload = {"data": {"type": "bots", "attributes": self.draft}}
        payload_path = self.c.debug_dir / "last_platform_payload.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"payload: {payload_path}")
        if self.c.dry_run:
            print("Dry run: payload validated; platform write skipped.")
            return {"published": False, "dryRun": True, "payload": str(payload_path)}
        if self.c.existing_bot_id:
            url = f"{self.c.base_url}/api/v3/nocode/bots/{self.c.existing_bot_id}/import-version/"
        else: url = f"{self.c.base_url}/api/v3/nocode/bots/import/"
        status, data = self.request("POST", url, payload)
        self.last_response = data
        (self.c.debug_dir / "last_platform_response.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"POST status: {status}")
        if not 200 <= status < 300: return {"published": False, "status": status, "response": data}
        attrs = (data.get("data") or {}).get("attributes") or {} if isinstance(data, dict) else {}
        bot_id, version_id = attrs.get("botId") or attrs.get("id"), attrs.get("id") or attrs.get("currentVersionId")
        if self.c.existing_bot_id and version_id:
            self.request("POST", f"{self.c.base_url}/api/v3/nocode/bots/{self.c.existing_bot_id}/bot-versions/{version_id}/make-current/")
        if bot_id and version_id:
            print(f"Frontend URL: {self.c.frontend_url}/projects/{bot_id}?botVersionId={version_id}")
        return {"published": True, "status": status, "botId": bot_id, "versionId": version_id}

    def test(self, message: str | None) -> dict[str, Any]:
        attrs = ((self.last_response or {}).get("data") or {}).get("attributes") if isinstance(self.last_response, dict) else None
        if not isinstance(attrs, dict): return {"tested": False, "error": "no successful platform response"}
        bot_id, version_id = attrs.get("botId") or attrs.get("id"), attrs.get("id") or attrs.get("currentVersionId")
        if not bot_id or not version_id: return {"tested": False, "error": "platform did not return bot/version id"}
        body = {"data": {"type": "engine", "attributes": {"sessionId": f"vibe-{uuid.uuid4().hex}", "messageId": uuid.uuid4().hex, "callbackUrl": None, "uuid": {"sub": "vibe-agent", "userId": "vibe-agent"}, "payload": {"message": {"originalText": message or self.c.test_message}, "userContextData": {"user": {}}, "contextOverride": None}, "debug": True, "environmentId": None}}}
        status, data = self.request("POST", f"{self.c.base_url}/api/v3/nocode/bots/{bot_id}/bot-versions/{version_id}/engine/", body)
        print(f"TEST status: {status}")
        return {"tested": 200 <= status < 300, "status": status, "response": data}

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "platform_contract": return self.contract()
        if name == "inspect_existing_bot": return self.inspect()
        if name == "save_draft": return self.save_draft(arguments.get("bot"))
        if name == "validate_draft": return {"valid": not self.validate(self.draft), "errors": self.validate(self.draft)}
        if name == "publish_draft": return self.publish()
        if name == "test_published_bot": return self.test(arguments.get("message"))
        return {"error": f"unknown tool: {name}"}

    def run(self, prompt: str) -> None:
        if not self.c.llm_url or not self.c.llm_key or not self.c.model:
            raise RuntimeError("COTYPE_BASE_URL, COTYPE_API_KEY, and COTYPE_MODEL are required")
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]
        if self.c.history_file and Path(self.c.history_file).is_file():
            history = json.loads(Path(self.c.history_file).read_text(encoding="utf-8"))
            messages.append({"role": "user", "content": "Prior conversation context (use only if relevant):\n" + json.dumps(history[-12:], ensure_ascii=False)})
        for _ in range(self.c.max_turns):
            status, response = self.request("POST", f"{self.c.llm_url}/chat/completions", {"model": self.c.model, "messages": messages, "tools": TOOLS, "tool_choice": "auto", "temperature": 0.1}, llm=True)
            if not 200 <= status < 300: raise RuntimeError(f"LLM HTTP {status}: {response}")
            choice = (response.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            messages.append(message)
            calls = message.get("tool_calls") or []
            if not calls:
                print(str(message.get("content") or "Completed."))
                return
            for call in calls:
                fn = call.get("function") or {}
                try: args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError: args = {}
                result = self.call_tool(str(fn.get("name", "")), args)
                messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": json.dumps(result, ensure_ascii=False)})
        raise RuntimeError(f"agent reached max turns ({self.c.max_turns}) before completion")

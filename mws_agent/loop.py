from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .skills import PlatformSkill, read_skill


SYSTEM = """You are a careful tool-calling builder. Follow the attached work-style and platform
skills. Inspect before an edit, save a complete draft, validate it, publish only a valid draft, and
verify the result through its real interface. Do not invent results. Ask a concise question only when
the user's request is genuinely ambiguous. Never tailor instructions or code to benchmark examples."""

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
    platform_skill_dir: Path
    work_style_skill: Path

    @classmethod
    def from_env(cls, args: Any) -> "Config":
        root = Path(__file__).resolve().parents[1]
        source_root = Path(os.getenv("MTS_AGENT_DIR", root))
        skill_root = source_root / "skills" if (source_root / "skills").is_dir() else root / "skills"
        return cls(
            base_url=os.getenv("PLATFORM_BASE_URL", os.getenv("MTS_PLATFORM_BASE_URL", "http://5.188.27.251:18080")).rstrip("/"),
            frontend_url=os.getenv("PLATFORM_FRONTEND_URL", os.getenv("MTS_PLATFORM_FRONTEND_URL", "http://5.188.27.251:18080")).rstrip("/"),
            token=os.getenv("MTS_PLATFORM_TOKEN", ""), workspace=os.getenv("MTS_AI_WORKSPACE", "default"), account=os.getenv("MTS_AI_ACCOUNT", "default"),
            llm_url=os.getenv("COTYPE_BASE_URL", os.getenv("MWS_BASE_URL", "")).rstrip("/"),
            llm_key=os.getenv("COTYPE_API_KEY", os.getenv("MWS_API_KEY", "")), model=os.getenv("COTYPE_MODEL", os.getenv("COTYPE_MODEL_NAME", os.getenv("MWS_MODEL_NAME", ""))),
            dry_run=args.dry_run, existing_bot_id=args.existing_bot_id, existing_version_id=args.existing_version_id,
            max_turns=max(1, args.max_turns), test_message=args.test_message, debug_dir=root / "debug", history_file=args.history_file,
            platform_skill_dir=Path(os.getenv("MWS_AGENT_PLATFORM_SKILL", skill_root / "mws-nocode")),
            work_style_skill=Path(os.getenv("MWS_AGENT_WORK_STYLE_SKILL", skill_root / "quality-loop" / "SKILL.md")),
        )


class Agent:
    def __init__(self, config: Config):
        self.c = config
        self.draft: dict[str, Any] | None = None
        self.last_response: Any = None
        self.platform = PlatformSkill.load(config.platform_skill_dir)
        self.work_style = read_skill(config.work_style_skill)
        self.c.debug_dir.mkdir(exist_ok=True)

    def headers(self, llm: bool = False) -> dict[str, str]:
        if llm:
            return {"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {self.c.llm_key}"}
        names = self.platform.spec["headers"]
        result = {names["accept"]: "application/json", names["contentType"]: "application/json", names["workspace"]: self.c.workspace, names["requestId"]: uuid.uuid4().hex}
        if self.c.token and self.c.token != "not-required": result["Authorization"] = f"Bearer {self.c.token}"
        if self.c.account: result[names["account"]] = self.c.account
        return result

    def platform_url(self, route: str, **values: Any) -> str:
        return self.c.base_url + self.platform.route(route, **values)

    def envelope(self, attributes: dict[str, Any]) -> dict[str, Any]:
        spec = self.platform.spec["payload"]
        return {spec["dataKey"]: {spec["typeKey"]: spec["typeValue"], spec["attributesKey"]: attributes}}

    def attributes(self, response: Any) -> dict[str, Any]:
        spec = self.platform.spec["response"]
        if not isinstance(response, dict):
            return {}
        data = response.get(spec["dataKey"])
        return data.get(spec["attributes"], {}) if isinstance(data, dict) else {}

    def target(self, response: Any) -> tuple[Any, Any, Any]:
        attrs, spec = self.attributes(response), self.platform.spec["response"]
        def first(fields: list[str]) -> Any:
            return next((attrs.get(key) for key in fields if attrs.get(key) is not None), None)
        scenarios = attrs.get(spec["scenariosField"])
        scenario_id = scenarios[0].get(spec["scenarioIdField"]) if isinstance(scenarios, list) and scenarios and isinstance(scenarios[0], dict) else None
        return first(spec["botIdFields"]), first(spec["versionIdFields"]), scenario_id

    def frontend_link(self, bot_id: Any, version_id: Any, scenario_id: Any) -> str:
        if scenario_id is None:
            raise RuntimeError("Platform response has no scenario id for a frontend link")
        return self.c.frontend_url + self.platform.spec["frontend"]["path"].format(botId=bot_id, versionId=version_id, scenarioId=scenario_id)

    def request(self, method: str, url: str, body: Any = None, llm: bool = False) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self.headers(llm), method=method)
        attempts = 2 if llm else 1
        timeout = int(os.getenv("COTYPE_TIMEOUT", "90")) if llm else 120
        for attempt in range(attempts):
            try:
                with urllib.request.urlopen(req, timeout=timeout) as res:
                    text = res.read().decode("utf-8", "replace")
                    return res.status, json.loads(text) if text else {}
            except urllib.error.HTTPError as err:
                text = err.read().decode("utf-8", "replace")
                try: return err.code, json.loads(text)
                except json.JSONDecodeError: return err.code, {"error": text}
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                if attempt + 1 == attempts:
                    raise RuntimeError(f"{'LLM' if llm else 'platform'} request failed: {error}") from error
                print(f"LLM request failed ({error}); retrying once.", flush=True)
                time.sleep(1)
        raise AssertionError("unreachable")

    def validate(self, bot: Any) -> list[str]:
        if not isinstance(bot, dict): return ["draft must be a JSON object"]
        rules = self.platform.spec["validation"]
        errors: list[str] = []
        for field, minimum in rules["stringFields"].items():
            if not isinstance(bot.get(field), str) or len(bot[field].strip()) < int(minimum): errors.append(f"{field} must be a string of at least {minimum} characters")
        for field, value in rules["exactFields"].items():
            if bot.get(field) != value: errors.append(f"{field} must be {value!r}")
        for field, pattern in rules["patternFields"].items():
            if not isinstance(bot.get(field), str) or not re.fullmatch(pattern, bot[field]): errors.append(f"{field} has an invalid format")
        for field in rules["integerFields"]:
            if not isinstance(bot.get(field), int): errors.append(f"{field} must be an integer")
        for field, allowed in rules["enumFields"].items():
            if bot.get(field) not in allowed: errors.append(f"{field} must be one of: {', '.join(allowed)}")
        scenarios = bot.get(rules["scenariosField"])
        if not isinstance(scenarios, list) or not scenarios: return errors + ["at least one scenario is required"]
        ids: set[str] = set()
        for index, scenario in enumerate(scenarios):
            if not isinstance(scenario, dict): errors.append(f"scenario {index} must be an object"); continue
            if not scenario.get(rules["scenarioNameField"]): errors.append(f"scenario {index} has no name")
            edges = scenario.get(rules["entryEdgesField"])
            if not isinstance(edges, list) or not edges: errors.append(f"scenario {index} needs entryEdges")
            elif not any(all(edge.get(key) == value for key, value in rules["entryEvent"].items()) for edge in edges if isinstance(edge, dict)): errors.append(f"scenario {index} needs its required entry event")
            nodes = scenario.get(rules["nodesField"])
            if not isinstance(nodes, list) or not nodes: errors.append(f"scenario {index} needs nodes"); continue
            for node in nodes:
                if not isinstance(node, dict) or not node.get(rules["nodeIdField"]): errors.append(f"scenario {index} has a node without id"); continue
                node_id = str(node[rules["nodeIdField"]])
                if node_id in ids: errors.append(f"duplicate node id: {node_id}")
                ids.add(node_id)
                if not isinstance(node.get(rules["nodeNameField"]), str) or not node[rules["nodeNameField"]].strip(): errors.append(f"node {node_id} needs a name")
                blocks = node.get(rules["blocksField"])
                if not isinstance(blocks, list) or not blocks: errors.append(f"node {node_id} needs blocks")
                else:
                    for block in blocks:
                        if not isinstance(block, dict) or not block.get(rules["blockIdField"]) or not block.get(rules["blockTypeField"]): errors.append(f"node {node_id} has an invalid block")
                        elif block.get(rules["blockTypeField"]) == rules["answerType"] and not isinstance(block.get(rules["answerValueField"]), str): errors.append(f"answer block in {node_id} needs value")
        return errors

    def contract(self) -> dict[str, Any]:
        return {
            "mode": "update" if self.c.existing_bot_id else "create",
            "dryRun": self.c.dry_run,
            "contract": self.platform.spec["contract"],
            "payload": self.platform.spec["payload"],
            "validation": self.platform.spec["validation"],
        }

    def inspect(self) -> dict[str, Any]:
        if not self.c.existing_bot_id: return {"note": "No existing bot selected; create a new one."}
        status, data = self.request("GET", self.platform_url("bot", botId=self.c.existing_bot_id))
        return {"status": status, "data": data}

    def save_draft(self, bot: Any) -> dict[str, Any]:
        self.draft = bot if isinstance(bot, dict) else None
        errors = self.validate(bot)
        if self.draft:
            (self.c.debug_dir / "last_platform_payload.json").write_text(json.dumps(self.envelope(self.draft), ensure_ascii=False, indent=2), encoding="utf-8")
        return {"saved": bool(self.draft), "valid": not errors, "errors": errors}

    def publish(self) -> dict[str, Any]:
        if not self.draft: return {"published": False, "error": "no draft saved"}
        errors = self.validate(self.draft)
        if errors: return {"published": False, "errors": errors}
        payload = self.envelope(self.draft)
        payload_path = self.c.debug_dir / "last_platform_payload.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"payload: {payload_path}", flush=True)
        if self.c.dry_run:
            print("Dry run: payload validated; platform write skipped.", flush=True)
            return {"published": False, "dryRun": True, "payload": str(payload_path)}
        if self.c.existing_bot_id:
            url = self.platform_url("importVersion", botId=self.c.existing_bot_id)
        else: url = self.platform_url("import")
        status, data = self.request("POST", url, payload)
        self.last_response = data
        (self.c.debug_dir / "last_platform_response.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"POST status: {status}", flush=True)
        if not 200 <= status < 300: return {"published": False, "status": status, "response": data}
        bot_id, version_id, scenario_id = self.target(data)
        active_bot_id = self.c.existing_bot_id or bot_id
        if self.c.existing_bot_id and version_id:
            current_status, _ = self.request("POST", self.platform_url("makeCurrent", botId=self.c.existing_bot_id, versionId=version_id))
            print(f"MAKE CURRENT status: {current_status}", flush=True)
        if not active_bot_id or not version_id:
            return {"published": False, "status": status, "error": "platform did not return botId/versionId"}
        publish_status, publish_response = self.request("POST", self.platform_url("publish", botId=active_bot_id, versionId=version_id))
        print(f"PUBLISH status: {publish_status}", flush=True)
        if not 200 <= publish_status < 300:
            return {"published": False, "status": publish_status, "response": publish_response}
        if bot_id and version_id and scenario_id is not None:
            link = self.frontend_link(bot_id, version_id, scenario_id)
            print(f"Frontend URL: {link}", flush=True)
        return {"published": True, "status": status, "botId": bot_id, "versionId": version_id, "scenarioId": scenario_id}

    def test(self, message: str | None) -> dict[str, Any]:
        bot_id, version_id, _ = self.target(self.last_response)
        if not bot_id or not version_id: return {"tested": False, "error": "platform did not return bot/version id"}
        body = {"data": {"type": "engine", "attributes": {"sessionId": f"vibe-{uuid.uuid4().hex}", "messageId": uuid.uuid4().hex, "callbackUrl": None, "uuid": {"sub": "vibe-agent", "userId": "vibe-agent"}, "payload": {"message": {"originalText": message or self.c.test_message}, "userContextData": {"user": {}}, "contextOverride": None}, "debug": True, "environmentId": None}}}
        status, data = self.request("POST", self.platform_url("engine", botId=bot_id, versionId=version_id), body)
        if status >= 500:
            print(f"TEST status: {status}; retrying once after publication.", flush=True)
            time.sleep(1)
            status, data = self.request("POST", self.platform_url("engine", botId=bot_id, versionId=version_id), body)
        print(f"TEST status: {status}", flush=True)
        reply = self.reply_text(data)
        if reply:
            print(f"TEST reply: {reply[:500]}", flush=True)
        return {"tested": 200 <= status < 300, "status": status, "reply": reply, "response": data}

    def reply_text(self, data: Any) -> str:
        """Extract visible text from the engine envelope without exposing debug data."""
        try:
            items: Any = data
            for key in self.platform.spec["response"]["replyPath"]:
                items = items[key]
            values = [item.get("bubble", {}).get("value") for item in items if isinstance(item, dict)]
            return "\n".join(value for value in values if isinstance(value, str)).strip()
        except (KeyError, TypeError):
            return ""

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
        attached_skills = f"\n\n# Work-style skill\n{self.work_style}\n\n# Platform skill\n{self.platform.instructions}"
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM + attached_skills}, {"role": "user", "content": prompt}]
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
                print(f"TOOL: {fn.get('name', 'unknown')}", flush=True)
                try: args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError: args = {}
                result = self.call_tool(str(fn.get("name", "")), args)
                if fn.get("name") == "publish_draft" and result.get("published"):
                    test_result = self.test(self.c.test_message)
                    result["test"] = test_result
                messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": json.dumps(result, ensure_ascii=False)})
                # A dry run has no published target to test.  Its validated payload is
                # the terminal artefact, so do not spend more model turns seeking one.
                if fn.get("name") == "publish_draft" and result.get("dryRun"):
                    print("Dry-run completed: validated payload is ready for review.")
                    return
                if fn.get("name") == "publish_draft" and result.get("test", {}).get("tested"):
                    print("Published bot passed the engine smoke test.", flush=True)
                    return
        if self.c.dry_run and self.draft and not self.validate(self.draft):
            self.publish()
            print("Dry-run finalized after the turn limit with a valid draft.", flush=True)
            return
        raise RuntimeError(f"agent reached max turns ({self.c.max_turns}) before completion")

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from mws_agent.skills import PlatformSkill


class PlatformRuntime:
    """Stateful MWS implementation exposed only through the MCP server."""

    def __init__(self) -> None:
        root = Path(__file__).resolve().parents[1]
        source_root = Path(os.getenv("MTS_AGENT_DIR", root))
        skill_root = source_root / "skills" if (source_root / "skills").is_dir() else root / "skills"
        self.platform = PlatformSkill.load(Path(os.getenv("MWS_AGENT_PLATFORM_SKILL", skill_root / "mws-nocode")))
        self.base_url = os.getenv("PLATFORM_BASE_URL", os.getenv("MTS_PLATFORM_BASE_URL", "http://5.188.27.251:18080")).rstrip("/")
        self.frontend_url = os.getenv("PLATFORM_FRONTEND_URL", os.getenv("MTS_PLATFORM_FRONTEND_URL", self.base_url)).rstrip("/")
        self.token = os.getenv("MTS_PLATFORM_TOKEN", "")
        self.workspace = os.getenv("MTS_AI_WORKSPACE", "default")
        self.account = os.getenv("MTS_AI_ACCOUNT", "default")
        self.debug_dir = root / "debug"; self.debug_dir.mkdir(exist_ok=True)
        self.draft: dict[str, Any] | None = None
        self.last_response: Any = None
        self.context: dict[str, Any] = {"dryRun": True, "testMessage": "Hello"}

    def configure(self, value: dict[str, Any]) -> dict[str, Any]:
        self.context = {**self.context, **value}
        return {"configured": True, "mode": "update" if self.context.get("existingBotId") else "create", "dryRun": bool(self.context.get("dryRun"))}

    def headers(self) -> dict[str, str]:
        names = self.platform.spec["headers"]
        result = {names["accept"]: "application/json", names["contentType"]: "application/json", names["workspace"]: self.workspace, names["requestId"]: uuid.uuid4().hex}
        if self.token and self.token != "not-required": result["Authorization"] = f"Bearer {self.token}"
        if self.account: result[names["account"]] = self.account
        return result

    def url(self, route: str, **values: Any) -> str:
        return self.base_url + self.platform.route(route, **values)

    def request(self, method: str, url: str, body: Any = None) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=self.headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                text = response.read().decode("utf-8", errors="replace")
                return response.status, json.loads(text) if text else {}
        except urllib.error.HTTPError as error:
            text = error.read().decode("utf-8", errors="replace")
            try: return error.code, json.loads(text)
            except json.JSONDecodeError: return error.code, {"error": text}

    def envelope(self, attributes: dict[str, Any]) -> dict[str, Any]:
        spec = self.platform.spec["payload"]
        return {spec["dataKey"]: {spec["typeKey"]: spec["typeValue"], spec["attributesKey"]: attributes}}

    def attributes(self, response: Any) -> dict[str, Any]:
        spec = self.platform.spec["response"]
        data = response.get(spec["dataKey"]) if isinstance(response, dict) else None
        return data.get(spec["attributesKey"], {}) if isinstance(data, dict) else {}

    def target(self, response: Any) -> tuple[Any, Any, Any]:
        attrs, spec = self.attributes(response), self.platform.spec["response"]
        def first(fields: list[str]) -> Any: return next((attrs.get(key) for key in fields if attrs.get(key) is not None), None)
        scenarios = attrs.get(spec["scenariosField"])
        scenario_id = scenarios[0].get(spec["scenarioIdField"]) if isinstance(scenarios, list) and scenarios and isinstance(scenarios[0], dict) else None
        return first(spec["botIdFields"]), first(spec["versionIdFields"]), scenario_id

    def normalize(self, bot: Any) -> Any:
        if not isinstance(bot, dict): return bot
        def unicode_safe(item: Any) -> Any:
            if isinstance(item, str): return item.encode("utf-8", "replace").decode("utf-8")
            if isinstance(item, list): return [unicode_safe(value) for value in item]
            if isinstance(item, dict): return {str(key): unicode_safe(value) for key, value in item.items()}
            return item
        value = unicode_safe(bot); rules = self.platform.spec["validation"]; aliases = self.platform.spec["normalization"]["entryEdges"]
        for scenario in value.get(rules["scenariosField"], []):
            if not isinstance(scenario, dict): continue
            for edge in scenario.get(rules["entryEdgesField"], []):
                if not isinstance(edge, dict): continue
                target = aliases["targetField"]
                if not edge.get(target):
                    for alias in aliases["targetAliases"]:
                        if edge.get(alias): edge[target] = edge[alias]; break
                if edge.get(aliases["eventAlias"]):
                    edge.setdefault(aliases["typeField"], aliases["eventType"]); edge.setdefault(aliases["valueField"], edge[aliases["eventAlias"]])
        return value

    def validate(self, bot: Any) -> list[str]:
        if not isinstance(bot, dict): return ["draft must be a JSON object"]
        rules = self.platform.spec["validation"]; errors: list[str] = []
        for field, minimum in rules["stringFields"].items():
            if not isinstance(bot.get(field), str) or len(bot[field].strip()) < int(minimum): errors.append(f"{field} must be a string of at least {minimum} characters")
        for field, expected in rules["exactFields"].items():
            if bot.get(field) != expected: errors.append(f"{field} must be {expected!r}")
        for field, pattern in rules["patternFields"].items():
            if not isinstance(bot.get(field), str) or not re.fullmatch(pattern, bot[field]): errors.append(f"{field} has an invalid format")
        for field in rules["integerFields"]:
            if not isinstance(bot.get(field), int): errors.append(f"{field} must be an integer")
        for field, allowed in rules["enumFields"].items():
            if bot.get(field) not in allowed: errors.append(f"{field} must be one of: {', '.join(allowed)}")
        scenarios = bot.get(rules["scenariosField"])
        if not isinstance(scenarios, list) or not scenarios: return errors + ["at least one scenario is required"]
        for index, scenario in enumerate(scenarios):
            if not isinstance(scenario, dict): errors.append(f"scenario {index} must be an object"); continue
            edges, nodes = scenario.get(rules["entryEdgesField"]), scenario.get(rules["nodesField"])
            if not scenario.get(rules["scenarioNameField"]): errors.append(f"scenario {index} has no name")
            if not isinstance(edges, list) or not edges: errors.append(f"scenario {index} needs entryEdges")
            if not isinstance(nodes, list) or not nodes: errors.append(f"scenario {index} needs nodes"); continue
            node_ids = {str(node.get(rules["nodeIdField"])) for node in nodes if isinstance(node, dict) and node.get(rules["nodeIdField"])}
            target_key = rules["interactive"]["targetNodeField"]
            if not any(isinstance(edge, dict) and all(edge.get(k) == v for k, v in rules["entryEvent"].items()) for edge in edges or []): errors.append(f"scenario {index} needs its required entry event")
            for edge in edges or []:
                target = edge.get(target_key) if isinstance(edge, dict) else None
                if target is None or str(target) not in node_ids: errors.append(f"scenario {index} has an entry edge to an unknown node: {target}")
            for node in nodes:
                if not isinstance(node, dict) or not node.get(rules["nodeIdField"]): errors.append(f"scenario {index} has a node without id"); continue
                node_id = str(node[rules["nodeIdField"]]); blocks = node.get(rules["blocksField"])
                if not isinstance(node.get(rules["nodeNameField"]), str) or not node[rules["nodeNameField"]].strip(): errors.append(f"node {node_id} needs a name")
                if not isinstance(blocks, list) or not blocks: errors.append(f"node {node_id} needs blocks"); continue
                for block in blocks:
                    if not isinstance(block, dict) or not block.get(rules["blockIdField"]) or not block.get(rules["blockTypeField"]): errors.append(f"node {node_id} has an invalid block"); continue
                    if block.get(rules["blockTypeField"]) == rules["answerType"] and not isinstance(block.get(rules["answerValueField"]), str): errors.append(f"answer block in {node_id} needs value")
                    for required in rules.get("blockRequirements", {}).get(block.get(rules["blockTypeField"]), []):
                        if block.get(required) is None: errors.append(f"{block.get(rules['blockTypeField'])} block in {node_id} needs {required}")
                    if block.get(rules["blockTypeField"]) == "llm":
                        model_rule = rules.get("llmModel", {}); model = block.get(model_rule.get("field", "model"))
                        if not isinstance(model, dict): errors.append(f"llm block in {node_id} needs an object model config")
                        else:
                            for field in model_rule.get("requiredFields", []):
                                if not model.get(field): errors.append(f"llm model in {node_id} needs {field}")
                            for field, placeholder in model_rule.get("requiredPlaceholders", {}).items():
                                if model.get(field) != placeholder: errors.append(f"llm model in {node_id} must use {placeholder} for {field}")
                    if block.get(rules["blockTypeField"]) == rules["interactive"]["buttonsType"]:
                        buttons = block.get(rules["interactive"]["buttonsField"])
                        for button in buttons if isinstance(buttons, list) else []:
                            target = button.get(target_key) if isinstance(button, dict) else None
                            if not isinstance(button, dict) or not button.get(rules["interactive"]["buttonTitleField"]) or str(target) not in node_ids: errors.append(f"buttons block in {node_id} has an invalid target")
        return errors

    def contract(self) -> dict[str, Any]:
        return {"mode": "update" if self.context.get("existingBotId") else "create", "dryRun": bool(self.context.get("dryRun")), **{key: self.platform.spec[key] for key in ("payload", "validation", "normalization", "contract")}}

    def inspect(self) -> dict[str, Any]:
        bot_id = self.context.get("existingBotId")
        if not bot_id: return {"note": "No existing bot selected; create a new one."}
        status, data = self.request("GET", self.url("bot", botId=bot_id)); return {"status": status, "data": data}

    def save_draft(self, bot: Any) -> dict[str, Any]:
        self.draft = self.normalize(bot); errors = self.validate(self.draft)
        if self.draft: (self.debug_dir / "last_platform_payload.json").write_text(json.dumps(self.envelope(self.draft), ensure_ascii=False, indent=2), encoding="utf-8")
        return {"saved": bool(self.draft), "valid": not errors, "errors": errors}

    def engine_test(self, message: str | None = None) -> dict[str, Any]:
        bot_id, version_id, _ = self.target(self.last_response)
        if not bot_id or not version_id: return {"tested": False, "error": "no successful platform response"}
        body = {"data": {"type": "engine", "attributes": {"sessionId": f"vibe-{uuid.uuid4().hex}", "messageId": uuid.uuid4().hex, "callbackUrl": None, "uuid": {"sub": "vibe-agent", "userId": "vibe-agent"}, "payload": {"message": {"originalText": message or self.context.get("testMessage", "Hello")}, "userContextData": {"user": {}}, "contextOverride": None}, "debug": True, "environmentId": None}}}
        status, data = self.request("POST", self.url("engine", botId=bot_id, versionId=version_id), body)
        if status >= 500: time.sleep(1); status, data = self.request("POST", self.url("engine", botId=bot_id, versionId=version_id), body)
        try: reply = "\n".join(item.get("bubble", {}).get("value", "") for item in data["data"]["attributes"]["payload"].get("items", []))
        except (KeyError, TypeError): reply = ""
        return {"tested": 200 <= status < 300, "status": status, "reply": reply, "response": data}

    def publish(self) -> dict[str, Any]:
        if not self.draft: return {"terminal": False, "published": False, "error": "no draft saved"}
        errors = self.validate(self.draft)
        if errors: return {"terminal": False, "published": False, "errors": errors}
        payload = self.envelope(self.draft); payload_path = self.debug_dir / "last_platform_payload.json"; payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.context.get("dryRun"): return {"terminal": True, "published": False, "dryRun": True, "payload": str(payload_path)}
        existing = self.context.get("existingBotId"); status, data = self.request("POST", self.url("importVersion", botId=existing) if existing else self.url("import"), payload)
        self.last_response = data; (self.debug_dir / "last_platform_response.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if not 200 <= status < 300: return {"terminal": False, "published": False, "status": status, "response": data}
        bot_id, version_id, scenario_id = self.target(data); active = existing or bot_id
        if existing and version_id: self.request("POST", self.url("makeCurrent", botId=existing, versionId=version_id))
        if not active or not version_id: return {"terminal": False, "published": False, "error": "platform did not return bot/version id"}
        publish_status, publish_data = self.request("POST", self.url("publish", botId=active, versionId=version_id))
        if not 200 <= publish_status < 300: return {"terminal": False, "published": False, "status": publish_status, "response": publish_data}
        link = self.frontend_url + self.platform.spec["frontend"]["path"].format(botId=bot_id, versionId=version_id, scenarioId=scenario_id) if scenario_id is not None else ""
        test = self.engine_test(self.context.get("testMessage"))
        return {"terminal": bool(test.get("tested")), "published": True, "botId": bot_id, "versionId": version_id, "scenarioId": scenario_id, "frontendUrl": link, "test": test}

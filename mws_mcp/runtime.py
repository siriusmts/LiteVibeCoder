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
        self.last_draft_attempt: dict[str, Any] | None = None
        self.last_response: Any = None
        self.context: dict[str, Any] = {"dryRun": True, "testMessage": "Hello"}
        self.inspected_existing_bot_id: str | None = None
        self.run_artifacts: list[str] = []

    def configure(self, value: dict[str, Any]) -> dict[str, Any]:
        previous_run_id = self.context.get("runId")
        self.context = {**self.context, **value}
        self.context.setdefault("runId", uuid.uuid4().hex)
        if self.context["runId"] != previous_run_id:
            self.run_artifacts = []
        # An MCP process can serve more than one run. Do not let an inspection
        # from an earlier update authorize publication to a different bot.
        if self.inspected_existing_bot_id != str(self.context.get("existingBotId") or ""):
            self.inspected_existing_bot_id = None
        return {"configured": True, "mode": "update" if self.context.get("existingBotId") else "create", "dryRun": bool(self.context.get("dryRun"))}

    def artifact(self, name: str, value: Any) -> Path:
        """Keep a per-run debugging record while retaining the convenient latest file."""
        text = json.dumps(self.redact(value), ensure_ascii=False, indent=2)
        latest = self.debug_dir / name
        latest.write_text(text, encoding="utf-8")
        runs = self.debug_dir / "runs"; runs.mkdir(exist_ok=True)
        run_id = str(self.context.get("runId", "manual"))
        (runs / f"{run_id}_{name}").write_text(text, encoding="utf-8")
        if name not in self.run_artifacts:
            self.run_artifacts.append(name)
        manifest = {"runId": run_id, "artifacts": self.run_artifacts}
        manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2)
        (runs / f"{run_id}_manifest.json").write_text(manifest_text, encoding="utf-8")
        (self.debug_dir / "last_run.json").write_text(manifest_text, encoding="utf-8")
        return latest

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
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            return 599, {"error": f"platform request failed: {error}"}

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
            flow = rules.get("flow", {}); nodes = scenario.get(rules["nodesField"], [])
            if flow.get("repairSequentialWorkflow") and isinstance(nodes, list):
                for index, node in enumerate(nodes[:-1]):
                    if not isinstance(node, dict) or node.get(flow["nextNodeField"]): continue
                    blocks = node.get(rules["blocksField"], [])
                    types = {block.get(rules["blockTypeField"]) for block in blocks if isinstance(block, dict)}
                    if any(kind in flow.get("requiresNextFor", []) for kind in types):
                        next_node = nodes[index + 1]
                        if isinstance(next_node, dict) and next_node.get(rules["nodeIdField"]): node[flow["nextNodeField"]] = next_node[rules["nodeIdField"]]
            for node in nodes if isinstance(nodes, list) else []:
                for block in node.get(rules["blocksField"], []) if isinstance(node, dict) else []:
                    if not isinstance(block, dict) or block.get(rules["blockTypeField"]) not in {"llm", "agent"}: continue
                    model = block.get(rules.get("llmModel", {}).get("field", "model"))
                    if not isinstance(model, dict): continue
                    for key, raw in list(model.items()):
                        if not isinstance(raw, str): continue
                        for env_name, secret in os.environ.items():
                            if env_name.upper().endswith(("_URL", "_TOKEN", "_API_KEY", "_MODEL", "_MODEL_NAME")) and secret and raw == secret:
                                model[key] = "${" + env_name + "}"; break
        return value

    def materialize_model_env(self, bot: dict[str, Any]) -> dict[str, Any]:
        """Resolve only ${ENV_VAR} inside LLM model configs immediately before upload."""
        value = json.loads(json.dumps(bot)); rules = self.platform.spec["validation"]
        for scenario in value.get(rules["scenariosField"], []):
            for node in scenario.get(rules["nodesField"], []) if isinstance(scenario, dict) else []:
                for block in node.get(rules["blocksField"], []) if isinstance(node, dict) else []:
                    if not isinstance(block, dict) or block.get(rules["blockTypeField"]) not in {"llm", "agent"}: continue
                    model = block.get(rules.get("llmModel", {}).get("field", "model"))
                    if not isinstance(model, dict): continue
                    for key, raw in list(model.items()):
                        if isinstance(raw, str):
                            for env_name, secret in os.environ.items():
                                if env_name.upper().endswith(("_URL", "_TOKEN", "_API_KEY", "_MODEL", "_MODEL_NAME")) and secret and raw == secret:
                                    model[key] = "${" + env_name + "}"; raw = model[key]; break
                        match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", raw) if isinstance(raw, str) else None
                        if match:
                            env_name = match.group(1); env_name = rules.get("llmModel", {}).get("envAliases", {}).get(env_name, env_name)
                            if os.getenv(env_name): model[key] = os.environ[env_name]
        return value

    def redact(self, value: Any) -> Any:
        secrets = {os.getenv(name, "") for name in os.environ if name.endswith(("_API_KEY", "_TOKEN"))}
        def visit(item: Any) -> Any:
            if isinstance(item, str):
                for secret in secrets:
                    if secret and len(secret) > 3: item = item.replace(secret, "********")
                return item
            if isinstance(item, list): return [visit(entry) for entry in item]
            if isinstance(item, dict): return {key: visit(entry) for key, entry in item.items()}
            return item
        return visit(value)

    def is_duplicate_name_error(self, status: int, response: Any) -> bool:
        if status != 422:
            return False
        text = json.dumps(response, ensure_ascii=False).casefold()
        return "nonuniquenamevalueerror" in text or "already exists" in text

    def next_unique_bot_name(self) -> tuple[str, str]:
        if not self.draft:
            raise RuntimeError("no draft saved")
        previous = str(self.draft.get("botName", "bot"))
        suffix = "_" + uuid.uuid4().hex[:8]
        candidate = previous[: 64 - len(suffix)].rstrip("_") + suffix
        self.draft["botName"] = candidate
        return previous, candidate

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
            declared_node_ids = [str(node.get(rules["nodeIdField"])) for node in nodes if isinstance(node, dict) and node.get(rules["nodeIdField"])]
            if len(node_ids) != len(declared_node_ids): errors.append(f"scenario {index} has duplicate node ids")
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
                block_types = {block.get(rules["blockTypeField"]) for block in blocks if isinstance(block, dict)}
                if any(kind in rules.get("flow", {}).get("requiresNextFor", []) for kind in block_types):
                    next_id = node.get(rules["flow"]["nextNodeField"])
                    if next_id is None or str(next_id) not in node_ids: errors.append(f"workflow node {node_id} needs next_node_id to an existing node")
                def has_dollar_template(value: Any) -> bool:
                    marker = rules.get("templates", {}).get("forbiddenDollarPrefix", "${")
                    if isinstance(value, str):
                        return marker in value
                    if isinstance(value, list):
                        return any(has_dollar_template(item) for item in value)
                    if isinstance(value, dict):
                        return any(has_dollar_template(item) for item in value.values())
                    return False

                for block in blocks:
                    if not isinstance(block, dict) or not block.get(rules["blockIdField"]) or not block.get(rules["blockTypeField"]): errors.append(f"node {node_id} has an invalid block"); continue
                    block_type = block.get(rules["blockTypeField"])
                    if block_type not in rules.get("blockTypes", []):
                        errors.append(f"node {node_id} has an unknown block type: {block_type}")
                    if rules.get("flow", {}).get("disallowBlockNextNode") and rules.get("flow", {}).get("nextNodeField") in block:
                        errors.append(f"block in {node_id} must not contain next_node_id; put sequential routing on its node")
                    if block.get(rules["blockTypeField"]) == rules["answerType"] and (not isinstance(block.get(rules["answerValueField"]), str) or not block[rules["answerValueField"]].strip()): errors.append(f"answer block in {node_id} needs a non-empty value")
                    template_fields = ("value", "url", "body", "headers", "response_mapping", "system_message", "user_message")
                    if block.get(rules["blockTypeField"]) in {rules["answerType"], "http_request", "llm", "agent"} and any(has_dollar_template(block.get(field)) for field in template_fields):
                        errors.append(f"block in {node_id} uses unsupported ${{...}} template syntax; use {{{{scope.variable}}}}")
                    context_template = rules.get("templates", {}).get("forbiddenContextReferencePattern")
                    if context_template and block.get(rules["blockTypeField"]) in {rules["answerType"], "http_request", "llm", "agent"} and any(isinstance(block.get(field), str) and re.search(context_template, block[field]) for field in template_fields):
                        errors.append(f"block in {node_id} uses unsupported context template reference; use a scoped variable such as {{{{system.last_user_message}}}}")
                    for required in rules.get("blockRequirements", {}).get(block.get(rules["blockTypeField"]), []):
                        if block.get(required) is None: errors.append(f"{block.get(rules['blockTypeField'])} block in {node_id} needs {required}")
                    if block.get(rules["blockTypeField"]) == "script":
                        script_rule = rules.get("script", {}); pattern = script_rule.get("requiredPattern")
                        if pattern and (not isinstance(block.get("value"), str) or not re.search(pattern, block["value"])): errors.append(f"script block in {node_id} needs an async handler(context: Context)")
                        forbidden = script_rule.get("forbiddenPattern")
                        if forbidden and isinstance(block.get("value"), str) and re.search(forbidden, block["value"]): errors.append(f"script block in {node_id} uses a forbidden import")
                        for context_pattern in script_rule.get("forbiddenContextPatterns", []):
                            if isinstance(block.get("value"), str) and re.search(context_pattern, block["value"]):
                                errors.append(f"script block in {node_id} uses dictionary-style context access; use context.session.field or another direct scope attribute")
                                break
                        network = script_rule.get("forbiddenNetworkPattern")
                        if script_rule.get("networkAccess") is False and network and isinstance(block.get("value"), str) and re.search(network, block["value"], flags=re.IGNORECASE):
                            errors.append(f"script block in {node_id} cannot make outbound HTTP requests; use the native http_request block for REST APIs")
                    http_rule = rules.get("httpRequest", {})
                    if block.get(rules["blockTypeField"]) == http_rule.get("type", "http_request"):
                        method = block.get("method")
                        if not isinstance(method, str) or method.upper() not in http_rule.get("methods", []):
                            errors.append(f"http_request block in {node_id} needs a supported HTTP method")
                        if not isinstance(block.get("url"), str) or not block["url"].strip():
                            errors.append(f"http_request block in {node_id} needs a non-empty url")
                        timeout = block.get(http_rule.get("timeoutField", "timeout"))
                        if timeout is not None and (not isinstance(timeout, int) or timeout < 1):
                            errors.append(f"http_request block in {node_id} has an invalid timeout")
                        retries = block.get(http_rule.get("retriesField", "retry_attempts_count"))
                        if retries is not None and (not isinstance(retries, int) or retries < 0):
                            errors.append(f"http_request block in {node_id} has invalid retry_attempts_count")
                        for field in (http_rule.get("headersField", "headers"), http_rule.get("responseMappingField", "response_mapping")):
                            pairs = block.get(field)
                            if pairs is None:
                                continue
                            if not isinstance(pairs, list) or any(not isinstance(pair, dict) or not all(isinstance(pair.get(key), str) and pair[key].strip() for key in http_rule.get("keyValueFields", ["key", "value"])) for pair in pairs):
                                errors.append(f"http_request block in {node_id} has invalid {field}")
                        mappings = block.get(http_rule.get("responseMappingField", "response_mapping"))
                        key_pattern = http_rule.get("sessionKeyPattern")
                        if key_pattern and isinstance(mappings, list):
                            for pair in mappings:
                                if isinstance(pair, dict) and not (isinstance(pair.get("key"), str) and re.fullmatch(key_pattern, pair["key"])):
                                    errors.append(f"http_request block in {node_id} must map response values to explicit session.<field> keys")
                                    break
                        for field in (http_rule.get("successTargetField", "ok_target_node_id"), http_rule.get("errorTargetField", "error_target_node_id")):
                            target = block.get(field)
                            if target is not None and str(target) not in node_ids:
                                errors.append(f"http_request block in {node_id} has {field} pointing to an unknown node")
                    if block.get(rules["blockTypeField"]) in {"llm", "agent"}:
                        kind = block[rules["blockTypeField"]]
                        model_rule = rules.get("llmModel", {}); model = block.get(model_rule.get("field", "model"))
                        if not isinstance(model, dict): errors.append(f"{kind} block in {node_id} needs an object model config")
                        else:
                            for field in model_rule.get("requiredFields", []):
                                if not model.get(field): errors.append(f"{kind} model in {node_id} needs {field}")
                            pattern = model_rule.get("placeholderPattern")
                            if pattern:
                                for field in model_rule.get("requiredFields", []):
                                    if not isinstance(model.get(field), str) or not re.fullmatch(pattern, model[field]): errors.append(f"{kind} model in {node_id} must use an environment placeholder for {field}")
                        if kind == "agent":
                            servers = (block.get("tools") or {}).get("mcp_servers") if isinstance(block.get("tools"), dict) else None
                            if not isinstance(servers, list) or not all(isinstance(server, dict) and isinstance(server.get("url"), str) and server["url"].startswith(("http://", "https://")) for server in servers): errors.append(f"agent block in {node_id} needs tools.mcp_servers with HTTP URLs")
                    if block.get(rules["blockTypeField"]) == rules["interactive"]["buttonsType"]:
                        buttons = block.get(rules["interactive"]["buttonsField"])
                        if not isinstance(buttons, list) or not buttons: errors.append(f"buttons block in {node_id} needs at least one button")
                        for button in buttons if isinstance(buttons, list) else []:
                            target = button.get(target_key) if isinstance(button, dict) else None
                            if not isinstance(button, dict) or not button.get(rules["interactive"]["buttonTitleField"]) or str(target) not in node_ids: errors.append(f"buttons block in {node_id} has an invalid target")
                    if block.get(rules["blockTypeField"]) == "single_if":
                        target = block.get(target_key)
                        if str(target) not in node_ids: errors.append(f"single_if block in {node_id} has an invalid target")
                        allowed = rules.get("conditional", {}).get("allowedCodeTypes", [])
                        if allowed and block.get("code_type") not in allowed:
                            errors.append(f"single_if block in {node_id} must use code_type one of: {', '.join(allowed)}")
                        expression = block.get("expression")
                        for pattern in rules.get("conditional", {}).get("forbiddenExpressionPatterns", []):
                            if isinstance(expression, str) and re.search(pattern, expression):
                                errors.append(f"single_if block in {node_id} uses unsupported Python expression syntax; use the platform condition DSL")
            by_id = {str(node[rules["nodeIdField"]]): node for node in nodes if isinstance(node, dict) and node.get(rules["nodeIdField"])}
            reachable: set[str] = set()
            pending = [str(edge.get(target_key)) for edge in edges if isinstance(edge, dict) and str(edge.get(target_key)) in by_id]
            while pending:
                current = pending.pop()
                if current in reachable: continue
                reachable.add(current); node = by_id[current]
                next_id = node.get(rules.get("flow", {}).get("nextNodeField", "next_node_id"))
                if str(next_id) in by_id: pending.append(str(next_id))
                for block in node.get(rules["blocksField"], []):
                    if not isinstance(block, dict): continue
                    if block.get(rules["blockTypeField"]) == rules["interactive"]["buttonsType"]:
                        pending.extend(str(button.get(target_key)) for button in block.get(rules["interactive"]["buttonsField"], []) if isinstance(button, dict) and str(button.get(target_key)) in by_id)
                    if block.get(rules["blockTypeField"]) == "single_if" and str(block.get(target_key)) in by_id:
                        pending.append(str(block[target_key]))
                    if block.get(rules["blockTypeField"]) == rules.get("httpRequest", {}).get("type", "http_request"):
                        http_rule = rules.get("httpRequest", {})
                        for field in (http_rule.get("successTargetField", "ok_target_node_id"), http_rule.get("errorTargetField", "error_target_node_id")):
                            if str(block.get(field)) in by_id:
                                pending.append(str(block[field]))
            if unreachable := sorted(node_ids - reachable): errors.append(f"scenario {index} has unreachable nodes: {', '.join(unreachable)}")
        return errors

    def contract(self) -> dict[str, Any]:
        return {
            "mode": "update" if self.context.get("existingBotId") else "create",
            "dryRun": bool(self.context.get("dryRun")),
            "updatePrecondition": "A successful inspect_existing_bot call is required before publishing an update.",
            **{key: self.platform.spec[key] for key in ("payload", "validation", "normalization", "contract")},
        }

    def inspect(self) -> dict[str, Any]:
        bot_id = self.context.get("existingBotId")
        if not bot_id: return {"note": "No existing bot selected; create a new one."}
        status, data = self.request("GET", self.url("bot", botId=bot_id))
        if 200 <= status < 300:
            self.inspected_existing_bot_id = str(bot_id)
        return {"status": status, "data": data}

    def save_draft(self, bot: Any) -> dict[str, Any]:
        candidate = self.normalize(bot)
        if isinstance(candidate, dict) and self.last_draft_attempt:
            # Repair calls often contain only the fields that changed. Retain
            # omitted top-level platform fields from the immediately preceding
            # attempt, while arrays such as scenarios are deliberately replaced
            # as a complete graph.
            candidate = {**self.last_draft_attempt, **candidate}
        self.last_draft_attempt = candidate if isinstance(candidate, dict) else None
        errors = self.validate(candidate)
        if isinstance(candidate, dict):
            self.artifact("last_draft_attempt.json", self.envelope(candidate))
        if errors:
            # Keep the last valid draft available for repairs. Replacing it with
            # a malformed tool call forces the model to reconstruct the graph
            # from error text alone.
            return {
                "saved": False,
                "valid": False,
                "errors": errors,
                "repairDraft": candidate,
                "lastValidDraftAvailable": self.draft is not None,
            }
        self.draft = candidate
        self.artifact("last_platform_payload.json", self.envelope(self.draft))
        return {"saved": True, "valid": True, "errors": []}

    def get_saved_draft(self) -> dict[str, Any]:
        """Return the last valid draft so a failed live test can be repaired."""
        return {"available": self.draft is not None, "bot": self.draft}

    def engine_test(self, message: str | None = None, expect_contains: Any = None, expect_buttons: Any = None, expect_command: Any = None, session_id: str | None = None, expect_regex: Any = None, forbid_regex: Any = None) -> dict[str, Any]:
        bot_id, version_id, _ = self.target(self.last_response)
        if not bot_id:
            bot_id = self.context.get("existingBotId")
        if bot_id and not version_id:
            requested_version = self.context.get("existingVersionId")
            if requested_version:
                version_id = requested_version
            else:
                status, bot = self.request("GET", self.url("bot", botId=bot_id))
                version_id = self.attributes(bot).get("currentVersionId") if 200 <= status < 300 else None
        if not bot_id or not version_id: return {"tested": False, "error": "no successful platform response"}
        active_session_id = session_id or f"vibe-{uuid.uuid4().hex}"
        body = {"data": {"type": "engine", "attributes": {"sessionId": active_session_id, "messageId": uuid.uuid4().hex, "callbackUrl": None, "uuid": {"sub": "vibe-agent", "userId": "vibe-agent"}, "payload": {"message": {"originalText": message or self.context.get("testMessage", "Hello")}, "userContextData": {"user": {}}, "contextOverride": None}, "debug": True, "environmentId": None}}}
        status, data = self.request("POST", self.url("engine", botId=bot_id, versionId=version_id), body)
        if status >= 500: time.sleep(1); status, data = self.request("POST", self.url("engine", botId=bot_id, versionId=version_id), body)
        assertions = {"contains": False, "buttons": False, "command": False, "regex": False, "forbidden": False}
        buttons: list[str] = []; commands: list[str] = []
        try:
            payload = data["data"]["attributes"]["payload"]
            items = [item for item in payload.get("items", []) if isinstance(item, dict)]
            reply = "\n".join((item.get("bubble") or {}).get("value", "") for item in items)
            buttons = [button.get("title", "") for button in payload.get("suggestions", {}).get("buttons", [])]
            commands = [item["command"].get("value", "") for item in items if isinstance(item.get("command"), dict)]
        except (KeyError, TypeError): reply = ""
        else:
            expected_text = expect_contains if isinstance(expect_contains, list) else []
            expected_buttons = expect_buttons if isinstance(expect_buttons, list) else []
            expected_patterns = expect_regex if isinstance(expect_regex, list) else []
            forbidden_patterns = forbid_regex if isinstance(forbid_regex, list) else []
            def matches(pattern: Any) -> bool:
                if not isinstance(pattern, str): return False
                try: return bool(re.search(pattern, reply, flags=re.IGNORECASE))
                except re.error: return False
            assertions = {
                "contains": all(isinstance(value, str) and value.casefold() in reply.casefold() for value in expected_text),
                "buttons": all(isinstance(value, str) and value in buttons for value in expected_buttons),
                "command": not expect_command or expect_command in commands,
                "regex": all(matches(pattern) for pattern in expected_patterns),
                "forbidden": all(not matches(pattern) for pattern in forbidden_patterns),
            }
        engine_errors = self.engine_errors(data)
        awaiting_user = self.awaiting_user(data)
        technical = not reply or "техническая ошибка" in reply.lower() or "technical error" in reply.lower() or bool(engine_errors)
        tested = 200 <= status < 300 and not technical
        return {"sessionId": active_session_id, "tested": tested, "passed": tested and all(assertions.values()), "status": status, "reply": reply, "buttons": buttons, "commands": commands, "awaitingUser": awaiting_user, "assertions": assertions, "technical": technical, "engineErrors": engine_errors, "response": self.redact(data)}

    @staticmethod
    def engine_errors(data: Any) -> list[dict[str, str]]:
        """Extract a stable, compact diagnostic from an engine debug payload."""
        try:
            executions = data["data"]["attributes"].get("debug", {}).get("executions", [])
        except (KeyError, TypeError):
            return []
        errors: list[dict[str, str]] = []
        for execution in executions if isinstance(executions, list) else []:
            for node in execution.get("nodes", []) if isinstance(execution, dict) else []:
                for block in node.get("blocks", []) if isinstance(node, dict) else []:
                    if isinstance(block, dict) and block.get("isError"):
                        errors.append({"nodeId": str(node.get("nodeId", "")), "blockId": str(block.get("blockId", "")), "message": str(block.get("errorMessage", ""))[:800]})
                        if len(errors) == 3:
                            return errors
        return errors

    @staticmethod
    def awaiting_user(data: Any) -> bool:
        """Report whether the engine suspended the turn for another user action."""
        try:
            executions = data["data"]["attributes"].get("debug", {}).get("executions", [])
        except (KeyError, TypeError):
            return False
        for execution in executions if isinstance(executions, list) else []:
            for node in execution.get("nodes", []) if isinstance(execution, dict) else []:
                for block in node.get("blocks", []) if isinstance(node, dict) else []:
                    if isinstance(block, dict) and isinstance(block.get("result"), dict) and block["result"].get("is_interrupted") is True:
                        return True
        return False

    def verify(self, tests: Any) -> dict[str, Any]:
        if not isinstance(tests, list) or not tests:
            return {"terminal": False, "passed": False, "errors": ["tests must be a non-empty list"], "expectedCase": {"name": "concise case name", "steps": [{"message": "first user message"}]}}
        def valid_step(step: Any) -> bool:
            return isinstance(step, dict) and isinstance(step.get("message"), str) and bool(step["message"].strip())

        def has_assertion(step: dict[str, Any]) -> bool:
            return bool(step.get("expectContains") or step.get("expectRegex") or step.get("forbidRegex") or step.get("expectButtons") or step.get("expectCommand"))

        def case_error(case: Any, index: int) -> str | None:
            if not isinstance(case, dict) or not isinstance(case.get("name"), str) or not case["name"].strip():
                return f"case {index} needs a non-empty name"
            has_message = isinstance(case.get("message"), str) and bool(case["message"].strip())
            steps = case.get("steps")
            has_steps = isinstance(steps, list) and bool(steps) and all(valid_step(step) for step in steps)
            if has_message and has_steps:
                return f"case {index} has both message and steps; remove message or steps (keep steps for a stateful flow)"
            if not has_message and not has_steps:
                return f"case {index} needs exactly one of a non-empty message or non-empty steps"
            checks = [case] if has_message else steps
            if any(not has_assertion(step) for step in checks):
                return f"case {index} needs at least one observable assertion on every tested step"
            return None

        invalid = [error for index, case in enumerate(tests) if (error := case_error(case, index))]
        if invalid:
            return {
                "terminal": False,
                "passed": False,
                "errors": invalid,
                "expectedCase": {"name": "concise case name", "message": "one independent user message"},
                "expectedStatefulCase": {"name": "stateful path", "steps": [{"message": "first user message"}, {"message": "next user message"}]},
            }
        results = []
        for case in tests:
            if "message" in case:
                results.append({"name": case["name"], **self.engine_test(case["message"], case.get("expectContains"), case.get("expectButtons"), case.get("expectCommand"), expect_regex=case.get("expectRegex"), forbid_regex=case.get("forbidRegex"))})
                continue
            session_id = f"vibe-{uuid.uuid4().hex}"
            steps = [{"message": step["message"], **self.engine_test(step["message"], step.get("expectContains"), step.get("expectButtons"), step.get("expectCommand"), session_id, step.get("expectRegex"), step.get("forbidRegex"))} for step in case["steps"]]
            results.append({"name": case["name"], "tested": all(step["tested"] for step in steps), "passed": all(step["passed"] for step in steps), "steps": steps})
        passed = len(results) == len(tests) and all(result.get("passed") for result in results)
        engine_healthy = all(
            all(step.get("tested") for step in result.get("steps", [])) if "steps" in result else result.get("tested")
            for result in results
        )
        outcome = {"terminal": passed, "passed": passed, "engineHealthy": engine_healthy, "results": results}
        self.artifact("last_verification.json", {"tests": tests, **outcome})
        return outcome

    def publish(self) -> dict[str, Any]:
        if not self.draft: return {"terminal": False, "published": False, "error": "no draft saved"}
        existing = self.context.get("existingBotId")
        if existing and self.inspected_existing_bot_id != str(existing):
            return {"terminal": False, "published": False, "error": "inspect_existing_bot must successfully read the selected bot before publishing an update"}
        errors = self.validate(self.draft)
        if errors: return {"terminal": False, "published": False, "errors": errors}
        debug_payload = self.envelope(self.draft); payload = self.envelope(self.materialize_model_env(self.draft)); payload_path = self.artifact("last_platform_payload.json", debug_payload)
        if self.context.get("dryRun"): return {"terminal": True, "published": False, "dryRun": True, "payload": str(payload_path)}
        name_conflict_repairs = []
        for _ in range(3):
            status, data = self.request("POST", self.url("importVersion", botId=existing) if existing else self.url("import"), payload)
            self.last_response = data; self.artifact("last_platform_response.json", data)
            if existing or not self.is_duplicate_name_error(status, data):
                break
            previous, candidate = self.next_unique_bot_name()
            name_conflict_repairs.append({"previousBotName": previous, "botName": candidate})
            debug_payload = self.envelope(self.draft); payload = self.envelope(self.materialize_model_env(self.draft)); self.artifact("last_platform_payload.json", debug_payload)
        if not 200 <= status < 300: return {"terminal": False, "published": False, "status": status, "response": data}
        bot_id, version_id, scenario_id = self.target(data); active = existing or bot_id
        if not existing and active:
            # A repair after the first publish must create a new version of the
            # bot we just created, not create another bot. This run owns that
            # bot, so it is safe to authorize the follow-up update directly.
            self.context["existingBotId"] = str(active)
            self.inspected_existing_bot_id = str(active)
        if existing and version_id: self.request("POST", self.url("makeCurrent", botId=existing, versionId=version_id))
        if not active or not version_id: return {"terminal": False, "published": False, "error": "platform did not return bot/version id"}
        publish_status, publish_data = self.request("POST", self.url("publish", botId=active, versionId=version_id))
        if not 200 <= publish_status < 300: return {"terminal": False, "published": False, "status": publish_status, "response": publish_data}
        link = self.frontend_url + self.platform.spec["frontend"]["path"].format(botId=bot_id, versionId=version_id, scenarioId=scenario_id) if scenario_id is not None else ""
        test = self.engine_test(self.context.get("testMessage"))
        return {"terminal": False, "published": True, "botId": bot_id, "versionId": version_id, "scenarioId": scenario_id, "frontendUrl": link, "test": test, "nameConflictRepairs": name_conflict_repairs}

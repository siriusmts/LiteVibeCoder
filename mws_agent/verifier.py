"""Independent LLM-driven black-box verification for a published bot."""
from __future__ import annotations

import json
import os
from typing import Any, Callable


SYSTEM = """You are an independent black-box QA subagent for a published chatbot.
You see the original user request but never the implementation draft. Test only observable behavior
through the supplied tools. First call qa_set_plan and derive concise requirements from the request.
Then call qa_send_message exactly once per assistant turn, read the actual reply, buttons, commands,
awaitingUser, technical flag, and engineErrors, and choose the next human message from that evidence.
Use newSession=true only when starting an independent path. When buttons are shown, exercise the
relevant labels exactly. Cover the requested success paths, failure branches, state transitions,
integrations, and buttons without inventing requirements or benchmark-specific expectations.
Finally call qa_finish. Cite real turn IDs for every requirement. Pass only when every planned
requirement was observed and no technical error or requested-behavior mismatch remains. On failure,
give the builder concise factual issues: expected behavior, observed behavior, evidence turns, and a
repair suggestion. Never claim evidence that was not returned by qa_send_message."""


TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "qa_set_plan",
            "description": "Declare the observable requirements that this verification run will cover before sending test messages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "requirements": {
                        "type": "array",
                        "minItems": 1,
                        "items": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}, "description": {"type": "string"}},
                            "required": ["id", "description"],
                        },
                    }
                },
                "required": ["requirements"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qa_send_message",
            "description": "Send one human message to the published bot. The verifier owns session IDs; omit newSession to continue the active conversation.",
            "parameters": {
                "type": "object",
                "properties": {"message": {"type": "string"}, "newSession": {"type": "boolean"}},
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "qa_finish",
            "description": "Finish with a coverage-linked pass/fail verdict after testing every planned requirement.",
            "parameters": {
                "type": "object",
                "properties": {
                    "passed": {"type": "boolean"},
                    "summary": {"type": "string"},
                    "checks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "requirementId": {"type": "string"},
                                "passed": {"type": "boolean"},
                                "evidenceTurnIds": {"type": "array", "items": {"type": "integer"}},
                                "evidence": {"type": "string"},
                            },
                            "required": ["requirementId", "passed", "evidenceTurnIds", "evidence"],
                        },
                    },
                    "issues": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "requirementId": {"type": "string"},
                                "expected": {"type": "string"},
                                "observed": {"type": "string"},
                                "evidenceTurnIds": {"type": "array", "items": {"type": "integer"}},
                                "repair": {"type": "string"},
                            },
                            "required": ["requirementId", "expected", "observed", "evidenceTurnIds", "repair"],
                        },
                    },
                },
                "required": ["passed", "summary", "checks", "issues"],
            },
        },
    },
]


def compact_result(value: dict[str, Any]) -> str:
    result = {key: item for key, item in value.items() if key != "response"}
    text = json.dumps(result, ensure_ascii=False)
    return text if len(text) <= 8_000 else json.dumps({"truncated": True, "preview": text[:8_000]}, ensure_ascii=False)


class VerificationSubagent:
    def __init__(self, llm_request: Callable[[list[dict[str, Any]], list[dict[str, Any]]], dict[str, Any]], engine_test: Callable[[str, str | None], dict[str, Any]]) -> None:
        self.llm_request = llm_request
        self.engine_test = engine_test
        self.max_turns = max(4, int(os.getenv("MWS_VERIFIER_MAX_TURNS", "24")))

    @staticmethod
    def validate_plan(arguments: dict[str, Any]) -> tuple[dict[str, str], str | None]:
        items = arguments.get("requirements")
        if not isinstance(items, list) or not 1 <= len(items) <= 12:
            return {}, "requirements must contain between 1 and 12 items"
        plan: dict[str, str] = {}
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip() or not isinstance(item.get("description"), str) or not item["description"].strip():
                return {}, "each requirement needs a non-empty id and description"
            key = item["id"].strip()
            if key in plan:
                return {}, f"duplicate requirement id: {key}"
            plan[key] = item["description"].strip()
        return plan, None

    @staticmethod
    def validate_finish(arguments: dict[str, Any], plan: dict[str, str], turn_count: int) -> str | None:
        if not plan or turn_count < 1:
            return "set a plan and execute at least one real bot turn before finishing"
        checks = arguments.get("checks")
        issues = arguments.get("issues")
        if not isinstance(arguments.get("passed"), bool) or not isinstance(arguments.get("summary"), str) or not arguments["summary"].strip():
            return "passed must be boolean and summary must be non-empty"
        if not isinstance(checks, list) or not isinstance(issues, list):
            return "checks and issues must be arrays"
        by_id = {check.get("requirementId"): check for check in checks if isinstance(check, dict)}
        if set(by_id) != set(plan) or len(checks) != len(plan):
            return "checks must cover every planned requirement exactly once"
        for requirement_id, check in by_id.items():
            evidence_ids = check.get("evidenceTurnIds")
            if not isinstance(check.get("passed"), bool) or not isinstance(check.get("evidence"), str) or not check["evidence"].strip():
                return f"check {requirement_id} needs passed and non-empty evidence"
            if not isinstance(evidence_ids, list) or not evidence_ids or any(not isinstance(value, int) or value < 1 or value > turn_count for value in evidence_ids):
                return f"check {requirement_id} must cite real evidence turn IDs"
        passed = arguments["passed"]
        if passed and (issues or not all(check["passed"] for check in checks)):
            return "a passing verdict requires every check to pass and no issues"
        if not passed and (not issues or all(check["passed"] for check in checks)):
            return "a failing verdict requires at least one failed check and one issue"
        for issue in issues:
            if not isinstance(issue, dict) or issue.get("requirementId") not in plan:
                return "every issue must reference a planned requirement"
            evidence_ids = issue.get("evidenceTurnIds")
            if not isinstance(evidence_ids, list) or not evidence_ids or any(not isinstance(value, int) or value < 1 or value > turn_count for value in evidence_ids):
                return "every issue must cite real evidence turn IDs"
            if any(not isinstance(issue.get(field), str) or not issue[field].strip() for field in ("expected", "observed", "repair")):
                return "every issue needs expected, observed, and repair text"
        return None

    def run(self, user_request: str) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": "Verify the published bot against this request:\n" + user_request}]
        plan: dict[str, str] = {}
        active_session_id: str | None = None
        observations: list[dict[str, Any]] = []
        for _ in range(self.max_turns):
            response = self.llm_request(messages, TOOLS)
            message = ((response.get("choices") or [{}])[0].get("message") or {})
            messages.append(message)
            calls = message.get("tool_calls") or []
            if not calls:
                return {"completed": False, "passed": False, "verifierError": "QA subagent stopped without qa_finish", "turns": observations}
            for index, call in enumerate(calls):
                function = call.get("function") or {}; name = str(function.get("name", ""))
                print(f"QA TOOL: {name}", flush=True)
                parse_error: str | None = None
                try:
                    arguments = json.loads(function.get("arguments") or "{}")
                    if not isinstance(arguments, dict): raise ValueError("arguments must be an object")
                except (json.JSONDecodeError, ValueError) as error:
                    arguments = {}
                    parse_error = f"invalid tool arguments: {error}"
                if parse_error:
                    result = {"ok": False, "error": parse_error}
                elif index > 0:
                    result = {"ok": False, "error": "Call exactly one QA tool per assistant turn so the next action can depend on the latest bot response."}
                elif name == "qa_set_plan":
                    candidate, error = self.validate_plan(arguments)
                    if error: result = {"ok": False, "error": error}
                    elif plan: result = {"ok": False, "error": "the QA plan is already set"}
                    else: plan = candidate; result = {"ok": True, "requirements": plan}
                elif name == "qa_send_message":
                    text = arguments.get("message")
                    if not plan: result = {"ok": False, "error": "call qa_set_plan before testing"}
                    elif not isinstance(text, str) or not text.strip(): result = {"ok": False, "error": "message must be non-empty"}
                    else:
                        if arguments.get("newSession") is True: active_session_id = None
                        tested = self.engine_test(text.strip(), active_session_id)
                        if tested.get("sessionId"): active_session_id = str(tested["sessionId"])
                        observation = {"turnId": len(observations) + 1, "sent": text.strip(), **{key: tested.get(key) for key in ("sessionId", "tested", "reply", "buttons", "commands", "awaitingUser", "technical", "engineErrors", "assertions")}}
                        observations.append(observation)
                        result = observation
                elif name == "qa_finish":
                    error = self.validate_finish(arguments, plan, len(observations))
                    if error: result = {"ok": False, "error": error, "plannedRequirementIds": list(plan), "availableTurnIds": list(range(1, len(observations) + 1))}
                    else:
                        return {"completed": True, "passed": arguments["passed"], "summary": arguments["summary"].strip(), "requirements": plan, "checks": arguments["checks"], "issues": arguments["issues"], "turns": observations}
                else:
                    result = {"ok": False, "error": f"unknown QA tool: {name}"}
                messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": compact_result(result)})
        return {"completed": False, "passed": False, "verifierError": f"QA subagent reached max turns ({self.max_turns}) without qa_finish", "turns": observations}

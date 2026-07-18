from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .mcp_client import MCPClient
from .skills import read_skill
from .verifier import VerificationSubagent


SYSTEM = """You are a careful tool-calling chatbot builder. Use only tools discovered from the
attached MCP server. Follow the work-style and platform context provided to you. Inspect before an
update, save a complete draft, validate it, and publish only a valid draft. When the requested
behavior includes a user-facing menu or named buttons, implement actual buttons; text that merely
lists choices is insufficient. Never invent results or tailor the implementation to benchmark
examples."""


def compact_tool_result(result: Any) -> str:
    """Keep tool feedback useful without letting raw platform payloads exhaust context."""
    def visit(value: Any, depth: int = 0) -> Any:
        if isinstance(value, str):
            return value if len(value) <= 2_000 else value[:2_000] + "… [truncated]"
        if isinstance(value, list):
            items = [visit(item, depth + 1) for item in value[:24]]
            return items + ([f"… {len(value) - 24} more items omitted"] if len(value) > 24 else [])
        if isinstance(value, dict):
            compact: dict[str, Any] = {}
            for key, item in value.items():
                if key == "response":
                    error = item.get("error") if isinstance(item, dict) else None
                    compact["responseError"] = visit(error, depth + 1) if error else "raw response omitted; use status, reply, assertions, or errors"
                else:
                    compact[str(key)] = visit(item, depth + 1)
            return compact
        return value

    text = json.dumps(visit(result), ensure_ascii=False)
    limit = max(1_000, int(os.getenv("MWS_AGENT_TOOL_RESULT_CHARS", "12000")))
    if len(text) <= limit:
        return text
    return json.dumps({"truncated": True, "originalChars": len(text), "preview": text[:limit]}, ensure_ascii=False)


def compact_tool_arguments(arguments: dict[str, Any]) -> str:
    """Retain a repairable draft while bounding unusually large tool histories."""
    if isinstance(arguments.get("bot"), dict):
        # A normal no-code graph is small enough to remain in the next model
        # turn. Omitting it makes the model copy our summary as a new draft and
        # turns a single graph error into a permanent invalid-draft loop.
        text = json.dumps(arguments, ensure_ascii=False)
        if len(text) <= 16_000:
            return text
        bot = arguments["bot"]
        summary = {
            "name": bot.get("name"), "botName": bot.get("botName"),
            "scenarioCount": len(bot.get("scenarios", [])) if isinstance(bot.get("scenarios"), list) else None,
            "note": "full draft was sent to the MCP server and is omitted from conversation history because it exceeded 16000 characters; call get_saved_draft before repairing it",
        }
        return json.dumps({"bot": summary}, ensure_ascii=False)
    text = json.dumps(arguments, ensure_ascii=False)
    return text if len(text) <= 4_000 else json.dumps({"truncated": True, "originalChars": len(text)}, ensure_ascii=False)


def verification_fingerprint(result: dict[str, Any]) -> str:
    """Make repeated black-box failures comparable without run-specific IDs."""
    failures: list[dict[str, Any]] = []
    for case in result.get("results", []) if isinstance(result.get("results"), list) else []:
        if not isinstance(case, dict):
            continue
        steps = case.get("steps") if isinstance(case.get("steps"), list) else [case]
        for step in steps:
            if not isinstance(step, dict) or step.get("passed"):
                continue
            assertions = step.get("assertions") if isinstance(step.get("assertions"), dict) else {}
            failures.append({
                "case": str(case.get("name", "")),
                "technical": bool(step.get("technical")),
                "failedAssertions": sorted(str(name) for name, passed in assertions.items() if not passed),
                "engineErrors": step.get("engineErrors") if isinstance(step.get("engineErrors"), list) else [],
            })
            break
    return json.dumps(failures, ensure_ascii=False, sort_keys=True)


def subagent_failure_fingerprint(result: dict[str, Any]) -> str:
    """Compare observed QA failures without unstable plan IDs or prose."""
    packet = result.get("repairPacket") if isinstance(result.get("repairPacket"), dict) else {}
    turns = packet.get("evidenceTurns") if isinstance(packet.get("evidenceTurns"), list) else []
    observations = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        errors = turn.get("engineErrors") if isinstance(turn.get("engineErrors"), list) else []
        buttons = turn.get("buttons") if isinstance(turn.get("buttons"), list) else []
        observations.append({
            "sent": str(turn.get("sent", "")).strip().casefold(),
            "technical": bool(turn.get("technical")),
            "hasReply": bool(str(turn.get("reply") or "").strip()),
            "buttons": sorted(str(value).strip().casefold() for value in buttons if isinstance(value, str)),
            "awaitingUser": bool(turn.get("awaitingUser")),
            "engineErrors": sorted(json.dumps(error, ensure_ascii=False, sort_keys=True) for error in errors),
        })
    return json.dumps(sorted(observations, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True)), ensure_ascii=False, sort_keys=True)


def plan_covers_live_dialogue(tests: Any, turns: int) -> bool:
    """Require the final suite to replay a conversation it already explored."""
    if turns < 2 or not isinstance(tests, list):
        return True
    return any(isinstance(case, dict) and isinstance(case.get("steps"), list) and len(case["steps"]) >= turns for case in tests)


def compact_platform_context(context: dict[str, Any]) -> dict[str, Any]:
    """Expose execution rules, not publisher implementation details, to the model."""
    validation = context.get("validation") if isinstance(context.get("validation"), dict) else {}
    useful_validation = {
        key: validation[key]
        for key in (
            "stringFields", "integerFields", "exactFields", "patternFields", "enumFields",
            "blockTypes", "blockRequirements", "interactive", "httpRequest", "templates",
            "conditional", "script", "flow", "llmModel",
        )
        if key in validation
    }
    return {
        "instructions": context.get("instructions"),
        "mode": context.get("mode"), "dryRun": context.get("dryRun"),
        "updatePrecondition": context.get("updatePrecondition"),
        "payload": context.get("payload"),
        "validation": useful_validation,
        "contract": context.get("contract"),
    }


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
        source_root = Path(os.getenv("MTS_AGENT_DIR", root)); skill_root = source_root / "skills" if (source_root / "skills").is_dir() else root / "skills"
        return cls(
            base_url=os.getenv("PLATFORM_BASE_URL", os.getenv("MTS_PLATFORM_BASE_URL", "http://5.188.27.251:18080")).rstrip("/"),
            frontend_url=os.getenv("PLATFORM_FRONTEND_URL", os.getenv("MTS_PLATFORM_FRONTEND_URL", "http://5.188.27.251:18080")).rstrip("/"),
            token=os.getenv("MTS_PLATFORM_TOKEN", ""), workspace=os.getenv("MTS_AI_WORKSPACE", "default"), account=os.getenv("MTS_AI_ACCOUNT", "default"),
            # EVA supplies a local token-counting proxy for generation while
            # retaining COTYPE_BASE_URL for the model configuration uploaded to
            # the platform. Keep those two concerns separate.
            llm_url=os.getenv("COTYPE_GENERATION_BASE_URL", os.getenv("COTYPE_BASE_URL", os.getenv("MWS_BASE_URL", ""))).rstrip("/"), llm_key=os.getenv("COTYPE_API_KEY", os.getenv("MWS_API_KEY", "")), model=os.getenv("COTYPE_MODEL", os.getenv("COTYPE_MODEL_NAME", os.getenv("MWS_MODEL_NAME", ""))),
            dry_run=args.dry_run, existing_bot_id=args.existing_bot_id, existing_version_id=args.existing_version_id, max_turns=max(1, args.max_turns), test_message=args.test_message, debug_dir=root / "debug", history_file=args.history_file,
            platform_skill_dir=Path(os.getenv("MWS_AGENT_PLATFORM_SKILL", skill_root / "mws-nocode")), work_style_skill=Path(os.getenv("MWS_AGENT_WORK_STYLE_SKILL", skill_root / "quality-loop" / "SKILL.md")),
        )


class Agent:
    def __init__(self, config: Config):
        self.c = config
        self.work_style = read_skill(config.work_style_skill)

    def context(self) -> dict[str, Any]:
        return {
            "dryRun": self.c.dry_run,
            "existingBotId": self.c.existing_bot_id,
            "existingVersionId": self.c.existing_version_id,
            "testMessage": self.c.test_message,
            "runId": uuid.uuid4().hex,
        }

    def validate(self, bot: Any) -> list[str]:
        mcp = MCPClient()
        try:
            mcp.start(); mcp.configure({**self.context(), "dryRun": True})
            return mcp.call("save_draft", {"bot": bot}).get("errors", [])
        finally:
            mcp.stop()

    def save_qa_artifact(self, run_id: str, version_id: Any, result: dict[str, Any]) -> None:
        """Persist complete QA evidence separately from compact LLM feedback."""
        self.c.debug_dir.mkdir(parents=True, exist_ok=True)
        text = json.dumps(result, ensure_ascii=False, indent=2)
        (self.c.debug_dir / "last_qa_verification.json").write_text(text, encoding="utf-8")
        runs = self.c.debug_dir / "runs"; runs.mkdir(exist_ok=True)
        suffix = str(version_id or "unknown")
        (runs / f"{run_id}_qa_verification_{suffix}.json").write_text(text, encoding="utf-8")

    def llm_request(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], timeout_seconds: int | None = None, tool_choice: str | dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {"model": self.c.model, "messages": messages, "tools": tools, "tool_choice": tool_choice or "auto", "temperature": 0.1}
        thinking = os.getenv("LLM_ENABLE_THINKING", "").strip().lower()
        if thinking in {"true", "false"}:
            # vLLM-compatible Qwen deployments read this extension from their
            # chat template. Keep it opt-in because standard providers need not
            # recognize the field.
            payload["chat_template_kwargs"] = {"enable_thinking": thinking == "true"}
        streaming = os.getenv("LLM_STREAM", "").strip().lower() in {"1", "true", "yes"}
        if streaming:
            payload["stream"] = True
        # print('URL=',f"{self.c.llm_url}/chat/completions","model:", self.c.model)
        request = urllib.request.Request(f"{self.c.llm_url}/chat/completions", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {self.c.llm_key}"}, method="POST")
        for attempt in range(2):
            try:
                # Bot plans with several branches can legitimately take longer than a short
                # request timeout.  It remains operator-configurable for constrained runners.
                # A full tool contract plus a multi-node graph can take longer
                # than a short chat response, especially on shared model pools.
                # Operators can still lower this with COTYPE_TIMEOUT.
                request_timeout = timeout_seconds if timeout_seconds is not None else int(os.getenv("COTYPE_TIMEOUT", "600"))
                with urllib.request.urlopen(request, timeout=request_timeout) as response:
                    if streaming:
                        return self.read_streaming_response(response)
                    return json.loads(response.read().decode("utf-8", "replace"))
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                if attempt: raise RuntimeError(f"LLM request failed: {error}") from error
                print(f"LLM request failed ({error}); retrying once.", flush=True); time.sleep(1)
        raise AssertionError("unreachable")

    @staticmethod
    def read_streaming_response(response: Any) -> dict[str, Any]:
        """Collect OpenAI-compatible SSE deltas into the usual chat response."""
        message: dict[str, Any] = {"role": "assistant"}
        text: list[str] = []
        calls: dict[int, dict[str, Any]] = {}
        for raw in response:
            line = raw.decode("utf-8", "replace").strip() if isinstance(raw, bytes) else str(raw).strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                delta = ((json.loads(data).get("choices") or [{}])[0].get("delta") or {})
            except (json.JSONDecodeError, AttributeError, IndexError):
                continue
            if isinstance(delta.get("content"), str):
                text.append(delta["content"])
            for incoming in delta.get("tool_calls") or []:
                if not isinstance(incoming, dict):
                    continue
                index = int(incoming.get("index", 0))
                current = calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                if incoming.get("id"):
                    current["id"] = str(incoming["id"])
                function = incoming.get("function") if isinstance(incoming.get("function"), dict) else {}
                if function.get("name"):
                    current["function"]["name"] += str(function["name"])
                if function.get("arguments"):
                    current["function"]["arguments"] += str(function["arguments"])
        if text:
            message["content"] = "".join(text)
        if calls:
            message["tool_calls"] = [calls[index] for index in sorted(calls)]
        return {"choices": [{"message": message}]}

    def run(self, prompt: str) -> None:
        if not self.c.llm_url or not self.c.llm_key or not self.c.model: raise RuntimeError("COTYPE_BASE_URL, COTYPE_API_KEY, and COTYPE_MODEL are required")
        mcp = MCPClient()
        try:
            run_context = self.context()
            mcp.start(); mcp.configure(run_context)
            context = compact_platform_context(mcp.call(mcp.context_tool(), {}))
            is_create = not self.c.existing_bot_id
            use_verification_subagent = os.getenv("MWS_VERIFICATION_MODE", "subagent").strip().lower() != "legacy"
            mode_rules = (
                "This is a CREATE run. Do not call inspect_existing_bot: no bot is selected and it is not useful."
                if is_create else
                "This is an UPDATE run. Call inspect_existing_bot successfully before publishing."
            )
            if use_verification_subagent:
                mode_rules += " After every successful publication, the runtime starts an independent black-box QA subagent using the same configured model. Do not test the bot yourself. If publication feedback contains subagentVerification issues, inspect their real dialogue evidence, call get_saved_draft, save a targeted complete repair, and publish again. Completion is automatic only after the QA subagent passes every requested behavior."
            else:
                mode_rules += " A malformed verification plan is not a bot failure: correct and resubmit only verify_published_bot. If a valid verification plan reports failed behavior, inspect the live conversation and its engineErrors, then call get_saved_draft, save a repaired draft, publish it, and verify it again."
            system = SYSTEM + f"\n\n# Execution rules\n{mode_rules}\n\n# Work-style skill\n{self.work_style}\n\n# MCP platform context\n{json.dumps(context, ensure_ascii=False)}"
            messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
            verified = False
            publication_needs_verification = False
            verification_plan_repair_only = False
            behavior_repair_required = False
            repair_fetch_required = False
            repair_save_required = False
            repair_publish_only = False
            live_test_required = False
            live_session_id = ""
            live_turns = 0
            required_button_titles: list[str] = []
            diagnostic_test_required = False
            last_failure = ""
            repeated_failure_count = 0
            last_subagent_failure = ""
            repeated_subagent_failure_count = 0
            last_draft_failure = ""
            repeated_draft_failure_count = 0
            no_tool_response_count = 0
            pending_frontend_url = ""
            if self.c.history_file and Path(self.c.history_file).is_file(): messages.append({"role": "user", "content": "Prior conversation context:\n" + Path(self.c.history_file).read_text(encoding="utf-8")[-12000:]})
            tools = mcp.openai_tools()
            if is_create:
                tools = [tool for tool in tools if tool["function"]["name"] != "inspect_existing_bot"]
            if use_verification_subagent:
                tools = [tool for tool in tools if tool["function"]["name"] not in {"test_published_bot", "verify_published_bot"}]
            available_tools = [tool["function"]["name"] for tool in tools]
            for _ in range(self.c.max_turns):
                # A malformed suite is an argument-shape problem, not a bot
                # repair.  Giving the model only this tool prevents it from
                # spending turns editing or republishing an untested draft.
                force_tool_call = False
                if verification_plan_repair_only:
                    turn_tools = [tool for tool in tools if mcp.tool_role(tool["function"]["name"]) == "verification"]
                    required_action = "verify_published_bot"
                elif repair_fetch_required:
                    turn_tools = [tool for tool in tools if tool["function"]["name"] == "get_saved_draft"]
                    required_action = "get_saved_draft"
                    force_tool_call = True
                elif repair_save_required:
                    turn_tools = [tool for tool in tools if tool["function"]["name"] == "save_draft"]
                    required_action = "save_draft"
                    force_tool_call = True
                elif repair_publish_only:
                    turn_tools = [tool for tool in tools if mcp.tool_role(tool["function"]["name"]) == "publication"]
                    required_action = "publish_draft"
                    force_tool_call = True
                else:
                    turn_tools = tools
                    required_action = ""
                response = self.llm_request(messages, turn_tools, tool_choice="required") if force_tool_call else self.llm_request(messages, turn_tools)
                message = ((response.get("choices") or [{}])[0].get("message") or {}); messages.append(message)
                calls = message.get("tool_calls") or []
                if not calls:
                    if verified:
                        print(str(message.get("content") or "Completed.")); return
                    if required_action and no_tool_response_count < 2:
                        no_tool_response_count += 1
                        print(f"MAIN AGENT returned text instead of required {required_action}; retrying the required repair step.", flush=True)
                        messages.append({"role": "user", "content": f"Continue the repair now. Call {required_action}; do not answer with prose."})
                        continue
                    phase = f"required repair step {required_action}" if required_action else "publication and independent verification"
                    raise RuntimeError(f"main agent stopped before completing {phase}")
                no_tool_response_count = 0
                for call in calls:
                    function = call.get("function") or {}; name = str(function.get("name", ""))
                    role = mcp.tool_role(name)
                    required_live_call = name == "test_published_bot" and live_test_required
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                        if not isinstance(arguments, dict):
                            raise ValueError("arguments must be a JSON object")
                    except (json.JSONDecodeError, ValueError) as error:
                        arguments = {}
                        result = {"ok": False, "toolError": f"Invalid tool arguments: {error}", "availableTools": available_tools}
                    else:
                        if name not in available_tools:
                            result = {"ok": False, "toolError": f"Tool {name!r} is not available in this run mode", "availableTools": available_tools}
                        elif repair_fetch_required and name != "get_saved_draft":
                            result = {"ok": False, "toolError": "QA found published behavior defects. Call get_saved_draft before editing.", "availableTools": available_tools}
                        elif repair_save_required and name != "save_draft":
                            result = {"ok": False, "toolError": "Repair the fetched draft and call save_draft.", "availableTools": available_tools}
                        elif verification_plan_repair_only and role != "verification":
                            result = {"ok": False, "toolError": "The published bot has not been tested because the verification plan was invalid. Resubmit only verify_published_bot with a corrected tests array.", "availableTools": available_tools}
                        elif role == "verification" and live_test_required:
                            result = {"ok": False, "toolError": "Before final verification, start a live conversation with test_published_bot. Read its response and reuse its sessionId for the next turn when the bot is conversational.", "availableTools": available_tools}
                        elif role == "verification" and not plan_covers_live_dialogue(arguments.get("tests"), live_turns):
                            result = {"ok": False, "toolError": f"The live exploration used {live_turns} turns in one session. The final verification plan must include a stateful case with at least {live_turns} ordered steps that replays that observed dialogue.", "availableTools": available_tools}
                        elif name == "test_published_bot" and live_test_required and live_session_id:
                            supplied_message = arguments.get("message")
                            arguments["sessionId"] = live_session_id
                            if required_button_titles and supplied_message not in required_button_titles:
                                result = {"ok": False, "toolError": "The previous live response displayed buttons. Continue by sending one of its exact button labels as the next message.", "availableTools": available_tools}
                            else:
                                result = mcp.call(name, arguments)
                        elif diagnostic_test_required and name not in {"test_published_bot", "get_saved_draft"}:
                            result = {"ok": False, "toolError": "The same published verification failure repeated after repair. First use test_published_bot to inspect the live reply and engineErrors, then repair the saved draft from that evidence.", "availableTools": available_tools}
                        elif repair_publish_only and role != "publication":
                            result = {"ok": False, "toolError": "A repaired draft is valid and saved. Call publish_draft now; do not save it again.", "availableTools": available_tools}
                        elif role == "publication" and (publication_needs_verification or behavior_repair_required):
                            reason = "verify_published_bot must run after the latest successful publication before another publication" if publication_needs_verification else "A valid verification plan failed against the published bot. Save a repaired draft before publishing another version."
                            result = {"ok": False, "toolError": reason, "availableTools": available_tools}
                        else:
                            try:
                                result = mcp.call(name, arguments)
                            except Exception as error:
                                # A tool name can be hallucinated or a detachable MCP can reject an
                                # invocation.  Give the factual failure back to the model so it can
                                # select a discovered tool or repair its arguments on the next turn.
                                result = {"ok": False, "toolError": str(error), "availableTools": available_tools}
                                print(f"MCP TOOL failed: {name}: {error}", flush=True)
                    if use_verification_subagent and role == "publication" and result.get("published"):
                        def engine_test(message: str, session_id: str | None) -> dict[str, Any]:
                            test_arguments: dict[str, Any] = {"message": message}
                            if session_id:
                                test_arguments["sessionId"] = session_id
                            return mcp.call("test_published_bot", test_arguments)

                        def qa_llm_request(messages: list[dict[str, Any]], qa_tools: list[dict[str, Any]]) -> dict[str, Any]:
                            timeout = max(30, int(os.getenv("MWS_VERIFIER_LLM_TIMEOUT", "120")))
                            return self.llm_request(messages, qa_tools, timeout_seconds=timeout)

                        if result.get("frontendUrl"):
                            print(f"Frontend URL (published, QA pending): {result['frontendUrl']}", flush=True)
                        print(f"QA SUBAGENT: starting independent verification with {self.c.model}", flush=True)
                        verification = VerificationSubagent(qa_llm_request, engine_test, method_skill=self.work_style).run(prompt)
                        result["subagentVerification"] = verification
                        self.save_qa_artifact(str(run_context["runId"]), result.get("versionId"), verification)
                    if role == "verification" and not result.get("toolError") and not result.get("errors") and not result.get("passed"):
                        result["guidance"] = "Compare every failed assertion with the actual reply. Resubmit only a corrected verification plan when the expectation or session sequence was wrong; repair the draft only when the observed behavior violates the user's requirement."
                    print(f"MCP TOOL: {name}", flush=True)
                    if result.get("toolError"):
                        print(f"MCP TOOL rejected: {result['toolError']}", flush=True)
                    if result.get("errors"):
                        label = "Verification plan invalid" if role == "verification" else "DRAFT invalid"
                        print(f"{label}: {'; '.join(result['errors'])}", flush=True)
                    function["arguments"] = compact_tool_arguments(arguments)
                    messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": compact_tool_result(result)})
                    if role == "publication" and result.get("frontendUrl"):
                        pending_frontend_url = str(result["frontendUrl"])
                    if role == "verification" and not result.get("toolError"):
                        if result.get("errors"):
                            verification_plan_repair_only = True
                            publication_needs_verification = True
                            print("Bot was not tested; model must correct and resubmit only the verification plan.", flush=True)
                        elif result.get("passed"):
                            publication_needs_verification = False
                            verification_plan_repair_only = False
                            verified = True
                            if pending_frontend_url:
                                print(f"Frontend URL: {pending_frontend_url}", flush=True)
                            print("Verification suite passed.", flush=True)
                            return
                        else:
                            publication_needs_verification = False
                            verification_plan_repair_only = False
                            behavior_repair_required = True
                            failure = verification_fingerprint(result)
                            if failure and failure == last_failure:
                                repeated_failure_count += 1
                            else:
                                last_failure = failure
                                repeated_failure_count = 1
                            if repeated_failure_count >= 3:
                                raise RuntimeError("the same published verification failure repeated after two repairs; stopping instead of publishing another unchanged behavior")
                            if repeated_failure_count >= 2:
                                diagnostic_test_required = True
                            if result.get("engineHealthy"):
                                print("Published behavior did not meet verification assertions; use only requirements from the user, then repair the draft if the actual behavior is wrong.", flush=True)
                            else:
                                print("Published behavior failed verification; model must repair the draft and publish a new version.", flush=True)
                    if name == "save_draft":
                        if result.get("valid"):
                            repair_save_required = False
                            last_draft_failure = ""
                            repeated_draft_failure_count = 0
                            if behavior_repair_required:
                                behavior_repair_required = False
                                repair_publish_only = True
                        elif result.get("errors"):
                            repair_save_required = True
                            failure = json.dumps(result["errors"], ensure_ascii=False, sort_keys=True)
                            if failure == last_draft_failure:
                                repeated_draft_failure_count += 1
                            else:
                                last_draft_failure = failure
                                repeated_draft_failure_count = 1
                            if repeated_draft_failure_count >= 3:
                                raise RuntimeError("the same structural draft errors repeated three times; stopping instead of resubmitting an ineffective repair")
                    if name == "get_saved_draft" and result.get("available"):
                        repair_fetch_required = False
                        repair_save_required = True
                    if name == "test_published_bot" and result.get("tested"):
                        diagnostic_test_required = False
                    if name == "test_published_bot" and result.get("tested") and required_live_call:
                        session_id = str(result.get("sessionId", ""))
                        if not live_session_id:
                            live_session_id = session_id
                            live_turns = 1
                        elif session_id == live_session_id:
                            live_turns += 1
                        if required_button_titles:
                            required_button_titles = []
                            live_test_required = False
                        elif result.get("buttons"):
                            required_button_titles = [str(title) for title in result["buttons"] if isinstance(title, str) and title]
                            live_test_required = bool(required_button_titles)
                        elif result.get("awaitingUser") and live_turns < 2:
                            live_test_required = True
                        else:
                            live_test_required = False
                    if role == "publication" and result.get("published"):
                        repair_fetch_required = False
                        repair_save_required = False
                        repair_publish_only = False
                        smoke = result.get("test") or {}
                        if smoke.get("reply"):
                            print(f"SMOKE TEST reply: {str(smoke['reply'])[:500]}", flush=True)
                        print("SMOKE TEST passed." if smoke.get("tested") else "SMOKE TEST failed; the verification suite will provide repair feedback.", flush=True)
                        if use_verification_subagent:
                            publication_needs_verification = False
                            verification = result.get("subagentVerification") or {}
                            if not verification.get("completed"):
                                raise RuntimeError(f"QA subagent could not complete verification: {verification.get('verifierError', 'unknown verifier failure')}")
                            if verification.get("passed"):
                                verified = True
                                if pending_frontend_url:
                                    print(f"Frontend URL: {pending_frontend_url}", flush=True)
                                print(f"QA SUBAGENT passed: {verification.get('summary', 'all requested behavior was observed')}", flush=True)
                                return
                            behavior_repair_required = True
                            repair_fetch_required = True
                            fingerprint = subagent_failure_fingerprint(verification)
                            if fingerprint and fingerprint == last_subagent_failure:
                                repeated_subagent_failure_count += 1
                            else:
                                last_subagent_failure = fingerprint
                                repeated_subagent_failure_count = 1
                            if repeated_subagent_failure_count >= 3:
                                raise RuntimeError("the independent QA subagent observed the same failure after two repairs; stopping instead of repeating an ineffective publication loop")
                            print(f"QA SUBAGENT found bot defects: {verification.get('summary', 'see structured issues in tool feedback')}", flush=True)
                        else:
                            publication_needs_verification = True
                            live_test_required = True
                            live_session_id = ""
                            live_turns = 0
                            required_button_titles = []
                    if result.get("dryRun") and role == "publication":
                        print("Dry-run completed.", flush=True)
                        return
                    if result.get("terminal") and role != "publication":
                        if pending_frontend_url: print(f"Frontend URL: {pending_frontend_url}", flush=True)
                        test = result.get("test") or {}
                        if test.get("reply"): print(f"TEST reply: {test['reply'][:500]}", flush=True)
                        print("Run completed." if test.get("tested") or result.get("dryRun") else "Run stopped without a passing test.", flush=True)
                        return
            raise RuntimeError(f"agent reached max turns ({self.c.max_turns}) before completion")
        finally:
            mcp.stop()

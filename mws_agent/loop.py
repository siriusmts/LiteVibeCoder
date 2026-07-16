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


SYSTEM = """You are a careful tool-calling builder. Use only the tools discovered from the
attached MCP server. Follow the work-style and platform context provided to you. Inspect before an
edit, save a complete draft, validate it, and publish only a valid draft. After publication, derive
a coverage-driven verification suite from the user's requested behavior and run the MCP tool marked
as verification. Decide the number of cases from the distinct observable requirements, branches,
integrations, and safety behavior in the task; do not use a fixed count. Give every case a concise
name that states the covered path. Use ordered steps in one case when a behavior depends on prior
turns in the same session; otherwise use independent cases. If verification fails, inspect its factual feedback, repair the
draft, and repeat the necessary publish-and-verify cycle. Do not claim completion before verification
passes.
Never invent results or tailor instructions to benchmark examples."""


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
    """Retain a valid tool-call history without repeating a full draft every turn."""
    if isinstance(arguments.get("bot"), dict):
        bot = arguments["bot"]
        summary = {
            "name": bot.get("name"), "botName": bot.get("botName"),
            "scenarioCount": len(bot.get("scenarios", [])) if isinstance(bot.get("scenarios"), list) else None,
            "note": "full draft was sent to the MCP server and is omitted from conversation history",
        }
        return json.dumps({"bot": summary}, ensure_ascii=False)
    text = json.dumps(arguments, ensure_ascii=False)
    return text if len(text) <= 4_000 else json.dumps({"truncated": True, "originalChars": len(text)}, ensure_ascii=False)


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

    def llm_request(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {"model": self.c.model, "messages": messages, "tools": tools, "tool_choice": "auto", "temperature": 0.1}
        request = urllib.request.Request(f"{self.c.llm_url}/chat/completions", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {self.c.llm_key}"}, method="POST")
        for attempt in range(2):
            try:
                # Bot plans with several branches can legitimately take longer than a short
                # request timeout.  It remains operator-configurable for constrained runners.
                with urllib.request.urlopen(request, timeout=int(os.getenv("COTYPE_TIMEOUT", "180"))) as response:
                    return json.loads(response.read().decode("utf-8", "replace"))
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                if attempt: raise RuntimeError(f"LLM request failed: {error}") from error
                print(f"LLM request failed ({error}); retrying once.", flush=True); time.sleep(1)
        raise AssertionError("unreachable")

    def run(self, prompt: str) -> None:
        if not self.c.llm_url or not self.c.llm_key or not self.c.model: raise RuntimeError("COTYPE_BASE_URL, COTYPE_API_KEY, and COTYPE_MODEL are required")
        mcp = MCPClient()
        try:
            mcp.start(); mcp.configure(self.context())
            context = mcp.call(mcp.context_tool(), {})
            system = SYSTEM + f"\n\n# Work-style skill\n{self.work_style}\n\n# MCP platform context\n{json.dumps(context, ensure_ascii=False)}"
            messages: list[dict[str, Any]] = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
            verified = False
            publication_needs_verification = False
            pending_frontend_url = ""
            if self.c.history_file and Path(self.c.history_file).is_file(): messages.append({"role": "user", "content": "Prior conversation context:\n" + Path(self.c.history_file).read_text(encoding="utf-8")[-12000:]})
            available_tools = [tool["function"]["name"] for tool in mcp.openai_tools()]
            for _ in range(self.c.max_turns):
                response = self.llm_request(messages, mcp.openai_tools())
                message = ((response.get("choices") or [{}])[0].get("message") or {}); messages.append(message)
                calls = message.get("tool_calls") or []
                if not calls:
                    if verified:
                        print(str(message.get("content") or "Completed.")); return
                    raise RuntimeError("agent stopped before the MCP verification tool passed")
                for call in calls:
                    function = call.get("function") or {}; name = str(function.get("name", ""))
                    role = mcp.tool_role(name)
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                        if not isinstance(arguments, dict):
                            raise ValueError("arguments must be a JSON object")
                    except (json.JSONDecodeError, ValueError) as error:
                        arguments = {}
                        result = {"ok": False, "toolError": f"Invalid tool arguments: {error}", "availableTools": available_tools}
                    else:
                        if role == "publication" and publication_needs_verification:
                            result = {"ok": False, "toolError": "verify_published_bot must run after the latest successful publication before another publication", "availableTools": available_tools}
                        else:
                            try:
                                result = mcp.call(name, arguments)
                            except Exception as error:
                                # A tool name can be hallucinated or a detachable MCP can reject an
                                # invocation.  Give the factual failure back to the model so it can
                                # select a discovered tool or repair its arguments on the next turn.
                                result = {"ok": False, "toolError": str(error), "availableTools": available_tools}
                                print(f"MCP TOOL failed: {name}: {error}", flush=True)
                    print(f"MCP TOOL: {name}", flush=True)
                    if result.get("errors"): print(f"DRAFT invalid: {'; '.join(result['errors'])}", flush=True)
                    function["arguments"] = compact_tool_arguments(arguments)
                    messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": compact_tool_result(result)})
                    if role == "publication" and result.get("frontendUrl"):
                        pending_frontend_url = str(result["frontendUrl"])
                    if role == "verification":
                        publication_needs_verification = False
                        if result.get("passed"):
                            verified = True
                            if pending_frontend_url:
                                print(f"Frontend URL: {pending_frontend_url}", flush=True)
                            print("Verification suite passed.", flush=True)
                            return
                        print("Verification failed; model must repair and retry.", flush=True)
                    if role == "publication" and result.get("published"):
                        publication_needs_verification = True
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

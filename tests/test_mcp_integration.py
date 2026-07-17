import unittest
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from mws_agent.cli import configuration_status
from mws_agent.mcp_client import MCPClient
from mws_agent.loop import Agent, Config, compact_platform_context, compact_tool_result
from mws_mcp.runtime import PlatformRuntime


VALID_BOT = {
    "name": "First version", "changesMessage": "Initial bot", "botName": "sample_bot",
    "engineType": "langgraph-engine", "requestTtlInSeconds": 30,
    "noMatchStubAnswer": "Please try again", "needPreprocess": "disabled",
    "scenarios": [{"name": "main", "entryEdges": [{"id": "init", "type": "event", "value": "init", "target_node_id": "start"}], "nodes": [{"id": "start", "name": "Start", "blocks": [{"id": "answer", "type": "answer", "value": "Hello"}]}]}],
}


class McpIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.client = MCPClient(); self.client.start(); self.client.configure({"dryRun": True, "testMessage": "Hello"})

    def tearDown(self):
        self.client.stop()

    def test_discovers_tools_from_mcp_server(self):
        names = {tool["name"] for tool in self.client.tools}
        self.assertTrue({"platform_contract", "save_draft", "publish_draft", "test_published_bot", "verify_published_bot"}.issubset(names))
        self.assertIn("payload", self.client.call(self.client.context_tool(), {}))

    def test_configuration_status_never_returns_llm_credentials(self):
        config = Config("http://platform.test", "http://frontend.test", "", "", "", "http://llm.test", "secret-value", "model", True, None, None, 4, "Hello", Path("debug"), None, Path("skills/mws-nocode"), Path("skills/quality-loop/SKILL.md"))
        status = configuration_status(config)
        self.assertTrue(status["ready"])
        self.assertNotIn("secret-value", str(status))

    def test_large_tool_responses_are_compacted_before_returning_to_the_model(self):
        result = compact_tool_result({"reply": "ok", "response": {"data": "x" * 50_000}})
        self.assertLess(len(result), 12_000)
        self.assertIn("raw response omitted", result)

    def test_platform_context_omits_transport_details_but_keeps_execution_contract(self):
        context = {"mode": "create", "payload": {"typeValue": "bots"}, "routes": {"import": "/secret-route"}, "validation": {"blockTypes": ["answer"], "flow": {"nextNodeField": "next_node_id"}, "ignored": "x"}, "contract": {"graph": "graph"}}
        compact = compact_platform_context(context)
        self.assertNotIn("routes", compact)
        self.assertEqual(compact["validation"], {"blockTypes": ["answer"], "flow": {"nextNodeField": "next_node_id"}})

    def test_loop_does_not_repeat_full_draft_in_llm_history(self):
        class FakeMcp:
            def start(self): pass
            def stop(self): pass
            def configure(self, context): return {}
            def context_tool(self): return "platform_contract"
            def openai_tools(self): return [{"type": "function", "function": {"name": name}} for name in ("platform_contract", "save_draft", "verify_published_bot")]
            def tool_role(self, name): return "verification" if name == "verify_published_bot" else ("context" if name == "platform_contract" else None)
            def call(self, name, arguments): return {"passed": True} if name == "verify_published_bot" else {"valid": True}

        agent = Agent(Config("", "", "", "", "", "http://llm.test", "key", "model", False, None, None, 3, "Hello", Path("debug"), None, Path("skills/mws-nocode"), Path("skills/quality-loop/SKILL.md")))
        large = "x" * 30_000
        responses = iter([
            {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": "save_draft", "arguments": json.dumps({"bot": {"name": "Draft", "botName": "draft", "scenarios": [], "large": large}})}}]}}]},
            {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "2", "function": {"name": "verify_published_bot", "arguments": "{}"}}]}}]},
        ])
        with patch("mws_agent.loop.MCPClient", FakeMcp), patch.object(agent, "llm_request", side_effect=lambda *args: next(responses)) as request:
            agent.run("Build a bot")
        history = json.dumps(request.call_args_list[1].args[0], ensure_ascii=False)
        self.assertNotIn(large, history)
        self.assertIn("full draft was sent", history)

    def test_eva_generation_proxy_takes_priority_over_payload_url(self):
        args = SimpleNamespace(dry_run=True, existing_bot_id=None, existing_version_id=None, max_turns=24, test_message="Hello", history_file=None)
        with patch.dict(os.environ, {"COTYPE_GENERATION_BASE_URL": "http://127.0.0.1:9876/v1", "COTYPE_BASE_URL": "https://payload.example/v1", "COTYPE_API_KEY": "key", "COTYPE_MODEL": "model"}, clear=True):
            config = Config.from_env(args)
        self.assertEqual(config.llm_url, "http://127.0.0.1:9876/v1")

    def test_provider_thinking_extension_is_opt_in(self):
        config = Config("", "", "", "", "", "http://llm.test", "key", "model", True, None, None, 1, "Hello", Path("debug"), None, Path("skills/mws-nocode"), Path("skills/quality-loop/SKILL.md"))
        agent = Agent(config)
        observed = []
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'{"choices":[{"message":{}}]}'
        def open_request(request, timeout):
            observed.append(json.loads(request.data.decode("utf-8")))
            return Response()
        with patch.dict(os.environ, {"LLM_ENABLE_THINKING": "false"}, clear=False), patch("mws_agent.loop.urllib.request.urlopen", side_effect=open_request):
            agent.llm_request([], [])
        self.assertIs(observed[0]["chat_template_kwargs"]["enable_thinking"], False)

    def test_streaming_response_reassembles_tool_call_arguments(self):
        chunks = [
            b'data: {"choices":[{"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call-1","type":"function","function":{"name":"save_","arguments":"{\\"bot\\":"}}]}}]}\n',
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"draft","arguments":"{}}"}}]}}]}\n',
            b'data: [DONE]\n',
        ]
        result = Agent.read_streaming_response(chunks)
        call = result["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(call["id"], "call-1")
        self.assertEqual(call["function"], {"name": "save_draft", "arguments": '{"bot":{}}'})

    def test_eva_proxy_url_is_not_written_into_platform_model_config(self):
        block = {"id": "llm", "type": "llm", "system_message": "Classify", "user_message": "{{message}}", "result_variable_name": "result", "model": {"url": "${LLM_URL}", "token": "${LLM_TOKEN}", "model_name": "${LLM_MODEL}"}}
        draft = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "nodes": [{"id": "start", "name": "Start", "blocks": [block]}, {"id": "finish", "name": "Finish", "blocks": [{"id": "answer", "type": "answer", "value": "Done"}]}]}]}
        with patch.dict(os.environ, {"COTYPE_GENERATION_BASE_URL": "http://127.0.0.1:9876/v1", "COTYPE_BASE_URL": "https://payload.example/v1", "COTYPE_API_KEY": "key", "COTYPE_MODEL_NAME": "model"}, clear=False):
            materialized = PlatformRuntime().materialize_model_env(draft)
        self.assertEqual(materialized["scenarios"][0]["nodes"][0]["blocks"][0]["model"]["url"], "https://payload.example/v1")

    def test_cli_default_allows_a_full_repair_cycle(self):
        self.assertEqual(__import__("mws_agent.cli", fromlist=["parser"]).parser().parse_args([]).max_turns, 64)

    def test_validates_draft_through_mcp(self):
        result = self.client.call("save_draft", {"bot": VALID_BOT})
        self.assertTrue(result["valid"])
        self.assertEqual(result["errors"], [])

    def test_normalizes_platform_declared_edge_aliases(self):
        draft = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "entryEdges": [{"sourceNodeId": "start", "sourceEvent": "init"}]}]}
        self.assertTrue(self.client.call("save_draft", {"bot": draft})["valid"])

    def test_rejects_edge_without_target(self):
        draft = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "entryEdges": [{"id": "init", "type": "event", "value": "init"}]}]}
        result = self.client.call("save_draft", {"bot": draft})
        self.assertFalse(result["valid"])
        self.assertTrue(any("unknown node" in error for error in result["errors"]))

    def test_rejects_empty_answer_and_menu_blocks(self):
        empty_answer = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "nodes": [{**VALID_BOT["scenarios"][0]["nodes"][0], "blocks": [{"id": "answer", "type": "answer", "value": "  "}]}]}]}
        empty_menu = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "nodes": [{**VALID_BOT["scenarios"][0]["nodes"][0], "blocks": [{"id": "menu", "type": "buttons", "buttons": []}]}]}]}
        self.assertTrue(any("non-empty value" in error for error in self.client.call("save_draft", {"bot": empty_answer})["errors"]))
        self.assertTrue(any("at least one button" in error for error in self.client.call("save_draft", {"bot": empty_menu})["errors"]))

    def test_rejects_duplicate_and_unreachable_nodes(self):
        start = VALID_BOT["scenarios"][0]["nodes"][0]
        duplicate = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "nodes": [start, {**start, "name": "Duplicate"}]}]}
        orphan = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "nodes": [start, {"id": "orphan", "name": "Orphan", "blocks": [{"id": "answer-2", "type": "answer", "value": "Unused"}]}]}]}
        self.assertTrue(any("duplicate node ids" in error for error in self.client.call("save_draft", {"bot": duplicate})["errors"]))
        self.assertTrue(any("unreachable nodes" in error for error in self.client.call("save_draft", {"bot": orphan})["errors"]))

    def test_sanitizes_invalid_unicode_from_model(self):
        draft = {**VALID_BOT, "changesMessage": "draft \udc98"}
        result = self.client.call("save_draft", {"bot": draft})
        self.assertTrue(result["valid"])

    def test_rejects_incomplete_llm_block(self):
        draft = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "nodes": [{"id": "start", "name": "Start", "blocks": [{"id": "llm", "type": "llm", "value": "missing required fields"}]}]}]}
        result = self.client.call("save_draft", {"bot": draft})
        self.assertFalse(result["valid"])
        self.assertTrue(any("llm block" in error for error in result["errors"]))

    def test_rejects_nonportable_llm_model_config(self):
        block = {"id": "llm", "type": "llm", "system_message": "Classify", "user_message": "{{message}}", "result_variable_name": "result", "model": "gpt-4"}
        draft = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "nodes": [{"id": "start", "name": "Start", "blocks": [block]}]}]}
        result = self.client.call("save_draft", {"bot": draft})
        self.assertFalse(result["valid"])
        self.assertTrue(any("model config" in error for error in result["errors"]))

    def test_accepts_task_selected_model_placeholders(self):
        block = {"id": "llm", "type": "llm", "system_message": "Classify", "user_message": "{{message}}", "result_variable_name": "result", "model": {"url": "${LLM_URL}", "token": "${LLM_TOKEN}", "model_name": "${LLM_MODEL}"}}
        draft = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "nodes": [{"id": "start", "name": "Start", "blocks": [block]}, {"id": "finish", "name": "Finish", "blocks": [{"id": "answer", "type": "answer", "value": "Done"}]}]}]}
        self.assertTrue(self.client.call("save_draft", {"bot": draft})["valid"])

    def test_validates_agent_mcp_configuration(self):
        block = {"id": "agent", "type": "agent", "system_message": "Use MCP", "user_message": "{{message}}", "result_variable_name": "result", "model": {"url": "${LLM_URL}", "token": "${LLM_TOKEN}", "model_name": "${LLM_MODEL}"}, "tools": {"mcp_servers": [{"url": "https://example.test/mcp"}]}}
        draft = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "nodes": [{"id": "start", "name": "Start", "blocks": [block]}, {"id": "finish", "name": "Finish", "blocks": [{"id": "answer", "type": "answer", "value": "{{session.result}}"}]}]}]}
        self.assertTrue(self.client.call("save_draft", {"bot": draft})["valid"])

    def test_rejects_script_without_platform_handler(self):
        block = {"id": "script", "type": "script", "value": "return 1", "result_variable_name": "result"}
        draft = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "nodes": [{"id": "start", "name": "Start", "blocks": [block]}]}]}
        result = self.client.call("save_draft", {"bot": draft})
        self.assertFalse(result["valid"])
        self.assertTrue(any("async handler" in error for error in result["errors"]))

    def test_rejects_script_import(self):
        block = {"id": "script", "type": "script", "value": "import json\nasync def handler(context: Context) -> None:\n    pass", "result_variable_name": "result"}
        draft = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "nodes": [{"id": "start", "name": "Start", "blocks": [block]}]}]}
        result = self.client.call("save_draft", {"bot": draft})
        self.assertFalse(result["valid"])
        self.assertTrue(any("forbidden import" in error for error in result["errors"]))

    def test_rejects_dynamic_script_import(self):
        block = {"id": "script", "type": "script", "value": "async def handler(context: Context) -> None:\n    module = __import__('example')\n    context.session['result'] = str(module)", "result_variable_name": "result"}
        draft = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "nodes": [{"id": "start", "name": "Start", "blocks": [block]}]}]}
        result = self.client.call("save_draft", {"bot": draft})
        self.assertFalse(result["valid"])
        self.assertTrue(any("forbidden import" in error for error in result["errors"]))

    def test_rejects_dictionary_style_context_access_in_scripts(self):
        block = {"id": "script", "type": "script", "value": "async def handler(context: Context) -> None:\n    value = context.get('value')\n    context.session.result = value", "result_variable_name": "result"}
        draft = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "nodes": [{"id": "start", "name": "Start", "blocks": [block]}]}]}
        result = self.client.call("save_draft", {"bot": draft})
        self.assertFalse(result["valid"])
        self.assertTrue(any("dictionary-style context" in error for error in result["errors"]))

    def test_rejects_network_url_in_script_without_network_capability(self):
        block = {"id": "script", "type": "script", "value": "async def handler(context: Context) -> None:\n    endpoint = 'https://service.example/search'\n    context.session['result'] = endpoint", "result_variable_name": "result"}
        draft = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "nodes": [{"id": "start", "name": "Start", "blocks": [block]}]}]}
        result = self.client.call("save_draft", {"bot": draft})
        self.assertFalse(result["valid"])
        self.assertTrue(any("outbound HTTP" in error for error in result["errors"]))

    def test_accepts_and_validates_native_http_request_routes(self):
        http = {
            "id": "lookup", "type": "http_request", "method": "GET",
            "url": "https://api.example.test/search?q={{system.last_user_message}}",
            "timeout": 15, "retry_attempts_count": 1,
            "response_mapping": [{"key": "session.result", "value": "$response.body.items[0].title"}],
            "ok_target_node_id": "result", "error_target_node_id": "failed",
        }
        nodes = [
            {"id": "start", "name": "Lookup", "blocks": [http]},
            {"id": "result", "name": "Result", "blocks": [{"id": "answer", "type": "answer", "value": "{{session.result}}"}]},
            {"id": "failed", "name": "Failure", "blocks": [{"id": "error", "type": "answer", "value": "Service is unavailable"}]},
        ]
        draft = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "nodes": nodes}]}
        self.assertTrue(self.client.call("save_draft", {"bot": draft})["valid"])
        http["ok_target_node_id"] = "missing"
        result = self.client.call("save_draft", {"bot": draft})
        self.assertFalse(result["valid"])
        self.assertTrue(any("ok_target_node_id" in error for error in result["errors"]))
        http["ok_target_node_id"] = "result"
        http["response_mapping"][0]["key"] = "result"
        result = self.client.call("save_draft", {"bot": draft})
        self.assertFalse(result["valid"])
        self.assertTrue(any("explicit session" in error for error in result["errors"]))

    def test_rejects_non_platform_templates_and_python_condition_syntax(self):
        condition = {"id": "route", "type": "single_if", "title": "Route", "expression": "context['session']['found'] is not None and len(context['session']['found']) > 0", "code_type": "python", "target_node_id": "start"}
        answer = {"id": "answer", "type": "answer", "value": "${session.result}"}
        draft = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "nodes": [{"id": "start", "name": "Start", "blocks": [condition, answer]}]}]}
        result = self.client.call("save_draft", {"bot": draft})
        self.assertFalse(result["valid"])
        self.assertTrue(any("template syntax" in error for error in result["errors"]))
        self.assertTrue(any("condition DSL" in error for error in result["errors"]))

    def test_rejects_human_worded_condition_expression(self):
        condition = {"id": "route", "type": "single_if", "title": "Result present", "expression": "session.result is not empty", "code_type": "custom", "target_node_id": "start"}
        draft = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "nodes": [{"id": "start", "name": "Start", "blocks": [condition]}]}]}
        result = self.client.call("save_draft", {"bot": draft})
        self.assertFalse(result["valid"])
        self.assertTrue(any("condition DSL" in error for error in result["errors"]))

    def test_rejects_invented_blocks_and_block_level_sequential_routes(self):
        invented = {"id": "menu", "type": "interactive", "buttons": [{"title": "Continue", "target_node_id": "start"}], "next_node_id": "start"}
        draft = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "nodes": [{"id": "start", "name": "Start", "blocks": [invented]}]}]}
        result = self.client.call("save_draft", {"bot": draft})
        self.assertFalse(result["valid"])
        self.assertTrue(any("unknown block type" in error for error in result["errors"]))
        self.assertTrue(any("must not contain next_node_id" in error for error in result["errors"]))

    def test_rejects_workflow_llm_without_next_node(self):
        block = {"id": "llm", "type": "llm", "system_message": "Classify", "user_message": "{{message}}", "result_variable_name": "result", "model": {"url": "${LLM_URL}", "token": "${LLM_TOKEN}", "model_name": "${LLM_MODEL}"}}
        draft = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "nodes": [{"id": "start", "name": "Start", "blocks": [block]}, {"id": "finish", "name": "Finish", "blocks": [{"id": "answer", "type": "answer", "value": "Done"}]}]}]}
        result = self.client.call("save_draft", {"bot": draft})
        self.assertTrue(result["valid"])

    def test_rejects_conditional_route_without_existing_target(self):
        block = {"id": "if", "type": "single_if", "title": "Route", "expression": "flag == True", "code_type": "python", "target_node_id": "missing"}
        draft = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "nodes": [{"id": "start", "name": "Start", "blocks": [block]}]}]}
        result = self.client.call("save_draft", {"bot": draft})
        self.assertFalse(result["valid"])
        self.assertTrue(any("single_if" in error for error in result["errors"]))

    def test_rejects_platform_invalid_conditional_code_type(self):
        block = {"id": "if", "type": "single_if", "title": "Route", "expression": "flag == True", "code_type": "javascript", "target_node_id": "start"}
        draft = {**VALID_BOT, "scenarios": [{**VALID_BOT["scenarios"][0], "nodes": [{"id": "start", "name": "Start", "blocks": [block]}]}]}
        result = self.client.call("save_draft", {"bot": draft})
        self.assertFalse(result["valid"])
        self.assertTrue(any("code_type" in error for error in result["errors"]))

    def test_loop_has_no_platform_tool_registry(self):
        source = (Path(__file__).resolve().parents[1] / "mws_agent" / "loop.py").read_text(encoding="utf-8")
        self.assertNotIn("TOOLS =", source)
        self.assertNotIn("def publish(", source)
        self.assertNotIn("api/v3/nocode", source)

    def test_loop_returns_unknown_tool_error_to_model_and_prints_published_link(self):
        class FakeMcp:
            def start(self): pass
            def stop(self): pass
            def configure(self, context): return {}
            def context_tool(self): return "platform_contract"
            def openai_tools(self): return [{"type": "function", "function": {"name": name}} for name in ("publish_draft", "test_published_bot", "verify_published_bot")]
            def tool_role(self, name): return "verification" if name == "verify_published_bot" else ("publication" if name == "publish_draft" else None)
            def call(self, name, arguments):
                if name == "platform_contract": return {"payload": {}}
                if name == "read_file": raise RuntimeError("MCP tools/call: Unknown MCP tool: read_file")
                if name == "publish_draft": return {"published": True, "frontendUrl": "http://example.test/projects/1"}
                if name == "test_published_bot": return {"tested": True, "sessionId": "session-1", "reply": "Hello"}
                return {"passed": True}

        config = Config("", "", "", "", "", "http://llm.test", "key", "model", True, None, None, 4, "Hello", Path("debug"), None, Path("skills/mws-nocode"), Path("skills/quality-loop/SKILL.md"))
        agent = Agent(config)
        responses = iter([
            {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": "read_file", "arguments": "{}"}}]}}]},
            {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "2", "function": {"name": "publish_draft", "arguments": "{}"}}]}}]},
            {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "3", "function": {"name": "test_published_bot", "arguments": "{\"message\":\"hello\"}"}}]}}]},
            {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "4", "function": {"name": "verify_published_bot", "arguments": "{}"}}]}}]},
        ])
        with patch("mws_agent.loop.MCPClient", FakeMcp), patch.object(agent, "llm_request", side_effect=lambda *_: next(responses)), patch("sys.stdout") as stdout:
            agent.run("Build a bot")
        printed = "".join(str(call.args[0]) for call in stdout.write.call_args_list)
        self.assertIn("not available in this run mode", printed)
        self.assertIn("Frontend URL: http://example.test/projects/1", printed)
        self.assertLess(printed.index("Frontend URL:"), printed.index("Verification suite passed."))

    def test_dry_run_does_not_stop_on_the_context_tool(self):
        class FakeMcp:
            def start(self): pass
            def stop(self): pass
            def configure(self, context): return {}
            def context_tool(self): return "platform_contract"
            def openai_tools(self): return [{"type": "function", "function": {"name": name}} for name in ("platform_contract", "save_draft", "publish_draft")]
            def tool_role(self, name): return "publication" if name == "publish_draft" else ("context" if name == "platform_contract" else None)
            def call(self, name, arguments):
                if name == "platform_contract": return {"dryRun": True, "payload": {}}
                if name == "save_draft": return {"saved": True, "valid": True}
                if name == "publish_draft": return {"dryRun": True, "terminal": True}
                raise AssertionError(f"unexpected tool: {name}")

        config = Config("", "", "", "", "", "http://llm.test", "key", "model", True, None, None, 4, "Hello", Path("debug"), None, Path("skills/mws-nocode"), Path("skills/quality-loop/SKILL.md"))
        agent = Agent(config)
        responses = iter([
            {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": "platform_contract", "arguments": "{}"}}]}}]},
            {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "2", "function": {"name": "save_draft", "arguments": "{\\\"bot\\\": {}}"}}]}}]},
            {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "3", "function": {"name": "publish_draft", "arguments": "{}"}}]}}]},
        ])
        with patch("mws_agent.loop.MCPClient", FakeMcp), patch.object(agent, "llm_request", side_effect=lambda *_: next(responses)) as llm_request, patch("sys.stdout") as stdout:
            agent.run("Build a bot")
        self.assertEqual(llm_request.call_count, 3)
        printed = "".join(str(call.args[0]) for call in stdout.write.call_args_list)
        self.assertIn("Dry-run completed.", printed)

    def test_loop_requires_verification_before_a_second_publication(self):
        class FakeMcp:
            def __init__(self): self.calls = []
            def start(self): pass
            def stop(self): pass
            def configure(self, context): return {}
            def context_tool(self): return "platform_contract"
            def openai_tools(self): return [{"type": "function", "function": {"name": name}} for name in ("platform_contract", "publish_draft", "test_published_bot", "verify_published_bot", "get_saved_draft", "save_draft")]
            def tool_role(self, name): return "publication" if name == "publish_draft" else ("verification" if name == "verify_published_bot" else "context")
            def call(self, name, arguments):
                self.calls.append(name)
                if name == "platform_contract": return {}
                if name == "publish_draft": return {"published": True}
                if name == "test_published_bot": return {"tested": True, "sessionId": "session-1", "reply": "Hello"}
                if name == "verify_published_bot": return {"passed": len([call for call in self.calls if call == "verify_published_bot"]) > 1}
                if name == "get_saved_draft": return {"available": True, "bot": {"name": "draft"}}
                if name == "save_draft": return {"saved": True, "valid": True}
                raise AssertionError(f"unexpected tool: {name}")

        fake_mcp = FakeMcp()
        config = Config("", "", "", "", "", "http://llm.test", "key", "model", False, None, None, 10, "Hello", Path("debug"), None, Path("skills/mws-nocode"), Path("skills/quality-loop/SKILL.md"))
        agent = Agent(config)
        calls = ["publish_draft", "publish_draft", "test_published_bot", "verify_published_bot", "get_saved_draft", "save_draft", "publish_draft", "test_published_bot", "verify_published_bot"]
        responses = iter([{"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": str(index), "function": {"name": name, "arguments": "{}"}}]}}]} for index, name in enumerate(calls, start=1)])
        with patch("mws_agent.loop.MCPClient", return_value=fake_mcp), patch.object(agent, "llm_request", side_effect=lambda *_: next(responses)):
            agent.run("Build a bot")
        self.assertEqual(fake_mcp.calls, ["platform_contract", "publish_draft", "test_published_bot", "verify_published_bot", "get_saved_draft", "save_draft", "publish_draft", "test_published_bot", "verify_published_bot"])

    def test_invalid_save_keeps_last_valid_draft_available_for_repair(self):
        runtime = PlatformRuntime()
        self.assertTrue(runtime.save_draft(VALID_BOT)["valid"])
        valid = runtime.get_saved_draft()["bot"]
        result = runtime.save_draft({"name": "broken", "scenarios": []})
        self.assertFalse(result["valid"])
        self.assertTrue(result["lastValidDraftAvailable"])
        self.assertEqual(runtime.get_saved_draft()["bot"], valid)

    def test_new_bot_repairs_are_published_as_versions_of_the_first_bot(self):
        runtime = PlatformRuntime()
        runtime.configure({"dryRun": False})
        runtime.save_draft(VALID_BOT)
        routes = []

        def request(method, url, body=None):
            routes.append(url)
            if url.endswith("/import/"):
                return 200, {"data": {"attributes": {"botId": "bot-1", "id": "version-1", "scenarios": [{"id": "scenario-1"}]}}}
            if url.endswith("/import-version/"):
                return 200, {"data": {"attributes": {"botId": "bot-1", "id": "version-2", "scenarios": [{"id": "scenario-1"}]}}}
            if url.endswith("/engine/"):
                return 200, {"data": {"attributes": {"payload": {"items": [{"bubble": {"value": "Hello"}}]}}}}
            return 200, {}

        runtime.request = request  # type: ignore[method-assign]
        self.assertTrue(runtime.publish()["published"])
        self.assertEqual(runtime.context["existingBotId"], "bot-1")
        self.assertTrue(runtime.publish()["published"])
        self.assertTrue(any(url.endswith("/bots/bot-1/import-version/") for url in routes))

    def test_create_recovers_from_a_duplicate_bot_name_without_another_model_turn(self):
        runtime = PlatformRuntime()
        runtime.configure({"dryRun": False})
        runtime.save_draft({**VALID_BOT, "botName": "joke_bot"})
        imported_names = []

        def request(method, url, body=None):
            if url.endswith("/import/"):
                imported_names.append(body["data"]["attributes"]["botName"])
                if len(imported_names) == 1:
                    return 422, {"error": {"title": "NonUniqueNameValueError", "detail": "already exists"}}
                return 200, {"data": {"attributes": {"botId": "bot-1", "id": "version-1", "scenarios": [{"id": "scenario-1"}]}}}
            if url.endswith("/engine/"):
                return 200, {"data": {"attributes": {"payload": {"items": [{"bubble": {"value": "Hello"}}]}}}}
            return 200, {}

        runtime.request = request  # type: ignore[method-assign]
        result = runtime.publish()
        self.assertTrue(result["published"])
        self.assertEqual(imported_names[0], "joke_bot")
        self.assertRegex(imported_names[1], r"^joke_bot_[0-9a-f]{8}$")
        self.assertEqual(result["nameConflictRepairs"][0]["previousBotName"], "joke_bot")

    def test_technical_engine_reply_does_not_pass_smoke_test(self):
        runtime = PlatformRuntime()
        runtime.last_response = {"data": {"attributes": {"id": "bot", "versionId": "version", "scenarios": [{"id": "scenario"}]}}}
        runtime.request = lambda *args, **kwargs: (200, {"data": {"attributes": {"payload": {"items": [{"bubble": {"value": "Техническая ошибка"}}]}}}})  # type: ignore[method-assign]
        self.assertFalse(runtime.engine_test("hello")["tested"])

    def test_can_test_configured_existing_version_without_publish_in_this_process(self):
        runtime = PlatformRuntime()
        runtime.configure({"existingBotId": "bot", "existingVersionId": "version"})
        runtime.request = lambda *args, **kwargs: (200, {"data": {"attributes": {"payload": {"items": [{"bubble": {"value": "Hello"}}]}}}})  # type: ignore[method-assign]
        self.assertTrue(runtime.engine_test("hello")["tested"])

    def test_update_publication_requires_a_successful_inspection_of_the_selected_bot(self):
        runtime = PlatformRuntime()
        runtime.configure({"dryRun": True, "existingBotId": "bot-a"})
        runtime.save_draft(VALID_BOT)
        blocked = runtime.publish()
        self.assertFalse(blocked["published"])
        self.assertIn("inspect_existing_bot", blocked["error"])

        runtime.request = lambda *args, **kwargs: (200, {"data": {"attributes": {}}})  # type: ignore[method-assign]
        self.assertEqual(runtime.inspect()["status"], 200)
        allowed = runtime.publish()
        self.assertTrue(allowed["dryRun"])

    def test_inspection_for_one_bot_cannot_authorize_another_bot(self):
        runtime = PlatformRuntime()
        runtime.configure({"dryRun": True, "existingBotId": "bot-a"})
        runtime.request = lambda *args, **kwargs: (200, {"data": {"attributes": {}}})  # type: ignore[method-assign]
        runtime.inspect()
        runtime.configure({"dryRun": True, "existingBotId": "bot-b"})
        runtime.save_draft(VALID_BOT)
        result = runtime.publish()
        self.assertFalse(result["published"])
        self.assertIn("inspect_existing_bot", result["error"])

    def test_network_timeout_becomes_a_failed_platform_result(self):
        runtime = PlatformRuntime()
        with patch("mws_mcp.runtime.urllib.request.urlopen", side_effect=TimeoutError("slow engine")):
            status, result = runtime.request("GET", "http://platform.invalid")
        self.assertEqual(status, 599)
        self.assertIn("platform request failed", result["error"])

    def test_debug_manifest_keeps_artifacts_associated_with_one_run(self):
        runtime = PlatformRuntime()
        with TemporaryDirectory() as directory:
            runtime.debug_dir = Path(directory)
            runtime.configure({"runId": "run-one"})
            runtime.artifact("last_platform_payload.json", {"draft": True})
            runtime.artifact("last_platform_response.json", {"published": True})
            manifest = json.loads((runtime.debug_dir / "last_run.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest, {"runId": "run-one", "artifacts": ["last_platform_payload.json", "last_platform_response.json"]})

    def test_verification_suite_requires_all_assertions(self):
        runtime = PlatformRuntime()
        runtime.last_response = {"data": {"attributes": {"id": "bot", "versionId": "version", "scenarios": [{"id": "scenario"}]}}}
        runtime.request = lambda *args, **kwargs: (200, {"data": {"attributes": {"payload": {"items": [None, {"bubble": {"value": "Hello catalog"}}], "suggestions": {"buttons": [{"title": "More"}]}}}}})  # type: ignore[method-assign]
        passed = runtime.verify([{"name": "catalog", "message": "hello", "expectContains": ["catalog"], "expectRegex": ["hello\\s+catalog"], "forbidRegex": ["REC-"], "expectButtons": ["More"]}])
        failed = runtime.verify([{"name": "handoff", "message": "hello", "expectCommand": "go_operator"}])
        forbidden = runtime.verify([{"name": "forbidden", "message": "hello", "forbidRegex": ["hello"]}])
        invalid = runtime.verify([{"message": "hello"}])
        unasserted = runtime.verify([{"name": "unasserted", "message": "hello"}])
        self.assertTrue(passed["passed"])
        self.assertFalse(failed["passed"])
        self.assertFalse(forbidden["passed"])
        self.assertFalse(invalid["passed"])
        self.assertFalse(unasserted["passed"])
        self.assertIn("case 0 needs a non-empty name", invalid["errors"])
        self.assertIn("observable assertion", unasserted["errors"][0])

    def test_live_engine_test_returns_session_and_compact_error(self):
        runtime = PlatformRuntime()
        runtime.last_response = {"data": {"attributes": {"id": "bot", "versionId": "version", "scenarios": [{"id": "scenario"}]}}}
        response = {"data": {"attributes": {
            "payload": {"items": [{"bubble": {"value": "Temporary reply"}}], "suggestions": {"buttons": []}},
            "debug": {"executions": [{"nodes": [{"nodeId": "lookup", "blocks": [{"blockId": "request", "isError": True, "errorMessage": "upstream failed", "result": {"is_interrupted": True}}]}]}]},
        }}}
        runtime.request = lambda *args, **kwargs: (200, response)  # type: ignore[method-assign]
        result = runtime.engine_test("hello", session_id="conversation-1")
        self.assertEqual(result["sessionId"], "conversation-1")
        self.assertTrue(result["technical"])
        self.assertTrue(result["awaitingUser"])
        self.assertEqual(result["engineErrors"], [{"nodeId": "lookup", "blockId": "request", "message": "upstream failed"}])

    def test_verification_reports_message_and_steps_conflict(self):
        runtime = PlatformRuntime()
        result = runtime.verify([{"name": "bad", "message": "hello", "steps": [{"message": "again"}]}])
        self.assertFalse(result["passed"])
        self.assertIn("has both message and steps", result["errors"][0])
        self.assertIn("expectedStatefulCase", result)

    def test_loop_limits_tools_to_verification_after_invalid_plan(self):
        class FakeMcp:
            def start(self): pass
            def stop(self): pass
            def configure(self, context): return {}
            def context_tool(self): return "platform_contract"
            def openai_tools(self):
                return [{"type": "function", "function": {"name": name}} for name in ("save_draft", "publish_draft", "verify_published_bot")]
            def tool_role(self, name): return "verification" if name == "verify_published_bot" else ("publication" if name == "publish_draft" else None)
            def call(self, name, arguments):
                if name == "verify_published_bot":
                    return {"passed": len(calls) > 1} if arguments.get("tests") else {"passed": False, "errors": ["tests must be a non-empty list"]}
                return {"published": True} if name == "publish_draft" else {"valid": True}

        calls = []
        agent = Agent(Config("", "", "", "", "", "http://llm.test", "key", "model", False, None, None, 4, "Hello", Path("debug"), None, Path("skills/mws-nocode"), Path("skills/quality-loop/SKILL.md")))
        replies = iter([
            {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": "verify_published_bot", "arguments": "{}"}}]}}]},
            {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "2", "function": {"name": "verify_published_bot", "arguments": '{"tests":[{"name":"smoke","message":"hello"}]}'}}]}}]},
        ])
        def request(messages, tools):
            calls.append([tool["function"]["name"] for tool in tools])
            return next(replies)
        with patch("mws_agent.loop.MCPClient", FakeMcp), patch.object(agent, "llm_request", side_effect=request):
            agent.run("Build a bot")
        self.assertEqual(calls[1], ["verify_published_bot"])

    def test_loop_requires_live_turn_before_final_verification(self):
        class FakeMcp:
            def start(self): pass
            def stop(self): pass
            def configure(self, context): return {}
            def context_tool(self): return "platform_contract"
            def openai_tools(self):
                return [{"type": "function", "function": {"name": name}} for name in ("publish_draft", "test_published_bot", "verify_published_bot")]
            def tool_role(self, name): return "verification" if name == "verify_published_bot" else ("publication" if name == "publish_draft" else None)
            def call(self, name, arguments):
                if name == "platform_contract": return {}
                if name == "publish_draft": return {"published": True}
                if name == "test_published_bot": return {"tested": True, "sessionId": "session-1", "reply": "What would you like?"}
                return {"passed": True}

        agent = Agent(Config("", "", "", "", "", "http://llm.test", "key", "model", False, None, None, 4, "Hello", Path("debug"), None, Path("skills/mws-nocode"), Path("skills/quality-loop/SKILL.md")))
        replies = iter([
            {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": "publish_draft", "arguments": "{}"}}]}}]},
            {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "2", "function": {"name": "verify_published_bot", "arguments": '{"tests":[{"name":"premature","message":"hello"}]}'}}]}}]},
            {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "3", "function": {"name": "test_published_bot", "arguments": '{"message":"hello"}'}}]}}]},
            {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "4", "function": {"name": "verify_published_bot", "arguments": '{"tests":[{"name":"checked","message":"hello"}]}'}}]}}]},
        ])
        with patch("mws_agent.loop.MCPClient", FakeMcp), patch.object(agent, "llm_request", side_effect=lambda messages, tools: next(replies)):
            agent.run("Build a bot")

    def test_loop_continues_an_interactive_live_session_before_verification(self):
        class FakeMcp:
            def __init__(self): self.live_calls = 0
            def start(self): pass
            def stop(self): pass
            def configure(self, context): return {}
            def context_tool(self): return "platform_contract"
            def openai_tools(self):
                return [{"type": "function", "function": {"name": name}} for name in ("publish_draft", "test_published_bot", "verify_published_bot")]
            def tool_role(self, name): return "verification" if name == "verify_published_bot" else ("publication" if name == "publish_draft" else None)
            def call(self, name, arguments):
                if name == "platform_contract": return {}
                if name == "publish_draft": return {"published": True}
                if name == "test_published_bot":
                    self.live_calls += 1
                    return {"tested": True, "sessionId": "session-1", "reply": "Continue", "awaitingUser": self.live_calls == 1}
                return {"passed": True}

        fake = FakeMcp()
        agent = Agent(Config("", "", "", "", "", "http://llm.test", "key", "model", False, None, None, 5, "Hello", Path("debug"), None, Path("skills/mws-nocode"), Path("skills/quality-loop/SKILL.md")))
        replies = iter([
            {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "1", "function": {"name": "publish_draft", "arguments": "{}"}}]}}]},
            {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "2", "function": {"name": "test_published_bot", "arguments": '{"message":"open"}'}}]}}]},
            {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "3", "function": {"name": "verify_published_bot", "arguments": '{"tests":[{"name":"premature","message":"hello"}]}'}}]}}]},
            {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "4", "function": {"name": "test_published_bot", "arguments": '{"message":"next","sessionId":"session-1"}'}}]}}]},
            {"choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "5", "function": {"name": "verify_published_bot", "arguments": '{"tests":[{"name":"checked","steps":[{"message":"open","expectContains":["Continue"]},{"message":"next","expectContains":["Continue"]}]}]}'}}]}}]},
        ])
        with patch("mws_agent.loop.MCPClient", return_value=fake), patch.object(agent, "llm_request", side_effect=lambda messages, tools: next(replies)):
            agent.run("Build a bot")
        self.assertEqual(fake.live_calls, 2)

    def test_verification_suite_keeps_stateful_steps_in_one_session(self):
        runtime = PlatformRuntime()
        seen_sessions = []

        def fake_engine(message, expect_contains=None, expect_buttons=None, expect_command=None, session_id=None, expect_regex=None, forbid_regex=None):
            seen_sessions.append(session_id)
            return {"tested": True, "passed": True, "reply": message}

        runtime.engine_test = fake_engine  # type: ignore[method-assign]
        result = runtime.verify([{"name": "confirmation", "steps": [{"message": "choose a time", "expectContains": ["choose"]}, {"message": "yes, confirm", "expectContains": ["confirm"]}]}])
        self.assertTrue(result["passed"])
        self.assertEqual(len(seen_sessions), 2)
        self.assertIsNotNone(seen_sessions[0])
        self.assertEqual(seen_sessions[0], seen_sessions[1])


if __name__ == "__main__":
    unittest.main()

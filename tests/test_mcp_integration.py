import unittest
from pathlib import Path

from mws_agent.mcp_client import MCPClient
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

    def test_loop_has_no_platform_tool_registry(self):
        source = (Path(__file__).resolve().parents[1] / "mws_agent" / "loop.py").read_text(encoding="utf-8")
        self.assertNotIn("TOOLS =", source)
        self.assertNotIn("def publish(", source)
        self.assertNotIn("api/v3/nocode", source)

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

    def test_verification_suite_requires_all_assertions(self):
        runtime = PlatformRuntime()
        runtime.last_response = {"data": {"attributes": {"id": "bot", "versionId": "version", "scenarios": [{"id": "scenario"}]}}}
        runtime.request = lambda *args, **kwargs: (200, {"data": {"attributes": {"payload": {"items": [None, {"bubble": {"value": "Hello catalog"}}], "suggestions": {"buttons": [{"title": "More"}]}}}}})  # type: ignore[method-assign]
        passed = runtime.verify([{"name": "catalog", "message": "hello", "expectContains": ["catalog"], "expectButtons": ["More"]}])
        failed = runtime.verify([{"name": "handoff", "message": "hello", "expectCommand": "go_operator"}])
        invalid = runtime.verify([{"message": "hello"}])
        self.assertTrue(passed["passed"])
        self.assertFalse(failed["passed"])
        self.assertFalse(invalid["passed"])


if __name__ == "__main__":
    unittest.main()

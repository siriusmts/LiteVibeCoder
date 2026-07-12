import unittest
from pathlib import Path

from mws_agent.mcp_client import MCPClient


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
        self.assertTrue({"platform_contract", "save_draft", "publish_draft", "test_published_bot"}.issubset(names))
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

    def test_loop_has_no_platform_tool_registry(self):
        source = (Path(__file__).resolve().parents[1] / "mws_agent" / "loop.py").read_text(encoding="utf-8")
        self.assertNotIn("TOOLS =", source)
        self.assertNotIn("def publish(", source)
        self.assertNotIn("api/v3/nocode", source)


if __name__ == "__main__":
    unittest.main()

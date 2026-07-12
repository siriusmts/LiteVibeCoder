import unittest
import json
import tempfile
from pathlib import Path

from mws_agent.loop import Agent, Config


class ValidationTests(unittest.TestCase):
    def agent(self):
        root = Path(__file__).resolve().parents[1]
        return Agent(Config("http://example", "http://example", "", "default", "default", "", "", "", True, None, None, 3, "hello", Path("debug"), None, root / "skills" / "mws-nocode", root / "skills" / "quality-loop" / "SKILL.md"))

    def test_rejects_incomplete_bot(self):
        self.assertTrue(self.agent().validate({"botName": "Bad Name"}))

    def test_accepts_minimum_graph_shape(self):
        bot = {"name": "First version", "changesMessage": "Initial bot", "botName": "sample_bot", "engineType": "langgraph-engine", "requestTtlInSeconds": 30, "noMatchStubAnswer": "Please try again", "needPreprocess": "disabled", "scenarios": [{"name": "main", "entryEdges": [{"id": "init", "type": "event", "value": "init", "target_node_id": "start"}], "nodes": [{"id": "start", "name": "Start", "next_node_id": None, "blocks": [{"id": "answer1", "tags": None, "type": "answer", "value": "Hello"}]}]}]}
        self.assertEqual(self.agent().validate(bot), [])

    def test_cli_validation_accepts_platform_wrapper(self):
        bot = {"name": "First version", "changesMessage": "Initial bot", "botName": "sample_bot", "engineType": "langgraph-engine", "requestTtlInSeconds": 30, "noMatchStubAnswer": "Please try again", "needPreprocess": "disabled", "scenarios": [{"name": "main", "entryEdges": [{"id": "init", "type": "event", "value": "init", "target_node_id": "start"}], "nodes": [{"id": "start", "name": "Start", "blocks": [{"id": "answer", "type": "answer", "value": "Hello"}]}]}]}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "payload.json"
            path.write_text(json.dumps({"data": {"attributes": bot}}), encoding="utf-8")
            from mws_agent.cli import main
            self.assertEqual(main(["--validate-payload", str(path)]), 0)

    def test_extracts_visible_engine_reply(self):
        response = {"data": {"attributes": {"payload": {"items": [{"bubble": {"value": "Hello"}}, {"bubble": {"value": "World"}}]}}}}
        self.assertEqual(self.agent().reply_text(response), "Hello\nWorld")

    def test_builds_frontend_link_with_active_scenario(self):
        self.assertEqual(self.agent().frontend_link(3325, 5991, 14083), "http://example/projects/3325?botVersionId=5991&activeScenarioId=14083")

    def test_loads_platform_route_from_skill_pack(self):
        self.assertEqual(self.agent().platform_url("publish", botId=1, versionId=2), "http://example/api/v3/nocode/bots/1/bot-versions/2/publish/")

    def test_extracts_target_with_scenario_from_skill_response(self):
        response = {"data": {"attributes": {"botId": 1, "id": 2, "scenarios": [{"id": 3}]}}}
        self.assertEqual(self.agent().target(response), (1, 2, 3))

    def test_rejects_entry_edge_without_target_node(self):
        bot = {"name": "First version", "changesMessage": "Initial bot", "botName": "sample_bot", "engineType": "langgraph-engine", "requestTtlInSeconds": 30, "noMatchStubAnswer": "Please try again", "needPreprocess": "disabled", "scenarios": [{"name": "main", "entryEdges": [{"id": "init", "type": "event", "value": "init"}], "nodes": [{"id": "start", "name": "Start", "blocks": [{"id": "answer", "type": "answer", "value": "Hello"}]}]}]}
        self.assertTrue(self.agent().validate(bot))

    def test_normalizes_skill_declared_entry_edge_aliases(self):
        bot = {"name": "First version", "changesMessage": "Initial bot", "botName": "sample_bot", "engineType": "langgraph-engine", "requestTtlInSeconds": 30, "noMatchStubAnswer": "Please try again", "needPreprocess": "disabled", "scenarios": [{"name": "main", "entryEdges": [{"sourceNodeId": "start", "sourceEvent": "init"}], "nodes": [{"id": "start", "name": "Start", "blocks": [{"id": "answer", "type": "answer", "value": "Hello"}]}]}]}
        normalized = self.agent().normalize_draft(bot)
        self.assertEqual(self.agent().validate(normalized), [])


if __name__ == "__main__":
    unittest.main()

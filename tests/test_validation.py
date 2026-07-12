import unittest

from mws_agent.loop import Agent, Config


class ValidationTests(unittest.TestCase):
    def agent(self):
        return Agent(Config("http://example", "http://example", "", "default", "default", "", "", "", True, None, None, 3, "hello", __import__("pathlib").Path("debug"), None))

    def test_rejects_incomplete_bot(self):
        self.assertTrue(self.agent().validate({"botName": "Bad Name"}))

    def test_accepts_minimum_graph_shape(self):
        bot = {"botName": "sample_bot", "requestTtlInSeconds": 30, "noMatchStubAnswer": "Please try again", "needPreprocess": "disabled", "scenarios": [{"name": "main", "entryEdges": [{"type": "event", "event": "init", "to": "start"}], "nodes": [{"id": "start", "blocks": [{"id": "answer1", "type": "answer", "value": "Hello"}]}]}]}
        self.assertEqual(self.agent().validate(bot), [])


if __name__ == "__main__":
    unittest.main()

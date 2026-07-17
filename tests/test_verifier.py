import io
import json
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from mws_agent.loop import Agent, Config
from mws_agent.verifier import VerificationSubagent


def tool_response(name, arguments, call_id="qa"):
    return {
        "choices": [{
            "message": {
                "role": "assistant",
                "tool_calls": [{
                    "id": call_id,
                    "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
                }],
            }
        }]
    }


class VerificationSubagentTests(unittest.TestCase):
    def test_verifier_owns_and_reuses_the_live_session(self):
        replies = iter([
            tool_response("qa_set_plan", {"requirements": [
                {"id": "welcome", "description": "Greets and asks for a dish"},
                {"id": "recipe", "description": "Shows a recipe and restart button"},
            ]}),
            tool_response("qa_send_message", {"message": "start", "requirementIds": ["welcome"], "purpose": "Open a fresh conversation"}),
            tool_response("qa_send_message", {"message": "Chicken", "requirementIds": ["recipe"], "purpose": "Exercise successful lookup"}),
            tool_response("qa_finish", {
                "passed": True,
                "summary": "Both requested paths were observed.",
                "checks": [
                    {"requirementId": "welcome", "passed": True, "evidenceTurnIds": [1], "evidence": "Greeting observed"},
                    {"requirementId": "recipe", "passed": True, "evidenceTurnIds": [2], "evidence": "Recipe and button observed"},
                ],
                "issues": [],
            }),
        ])
        supplied_sessions = []

        def engine(message, session_id):
            supplied_sessions.append(session_id)
            if session_id is None:
                return {"tested": True, "sessionId": "session-1", "reply": "Hello, enter a dish", "awaitingUser": True}
            return {"tested": True, "sessionId": "session-1", "reply": "Chicken, British, instructions", "buttons": ["Другой рецепт"]}

        verifier = VerificationSubagent(lambda messages, tools: next(replies), engine)
        result = verifier.run("Build a recipe bot")

        self.assertTrue(result["completed"])
        self.assertTrue(result["passed"])
        self.assertEqual(supplied_sessions, [None, "session-1"])

    def test_verifier_rejects_a_verdict_without_full_evidence_coverage(self):
        plan = {"welcome": "Greets", "recipe": "Returns recipe"}
        error = VerificationSubagent.validate_finish({
            "passed": True,
            "summary": "Looks good",
            "checks": [{"requirementId": "welcome", "passed": True, "evidenceTurnIds": [1], "evidence": "Observed"}],
            "issues": [],
        }, plan, 1)
        self.assertEqual(error, "checks must cover every planned requirement exactly once")

    def test_verifier_forces_finish_when_the_live_budget_is_exhausted(self):
        replies = iter([
            tool_response("qa_set_plan", {"requirements": [{"id": "reply", "description": "Returns a visible reply"}]}),
            tool_response("qa_send_message", {"message": "one", "requirementIds": ["reply"], "purpose": "First observation"}),
            tool_response("qa_send_message", {"message": "two", "requirementIds": ["reply"], "purpose": "Second observation"}),
            tool_response("qa_send_message", {"message": "three", "requirementIds": ["reply"], "purpose": "Final observation"}),
            tool_response("qa_finish", {
                "passed": True,
                "summary": "The requested reply was observed.",
                "checks": [{"requirementId": "reply", "passed": True, "evidenceTurnIds": [1], "evidence": "Visible reply"}],
                "issues": [],
            }),
        ])
        offered_tools = []

        def request(messages, tools):
            offered_tools.append([tool["function"]["name"] for tool in tools])
            return next(replies)

        verifier = VerificationSubagent(request, lambda message, session_id: {"tested": True, "sessionId": "s", "reply": message})
        result = verifier.run("Build an echo bot")

        self.assertTrue(result["passed"])
        self.assertEqual(len(result["turns"]), 3)
        self.assertEqual(offered_tools[-1], ["qa_finish"])

    def test_agent_repairs_from_subagent_feedback_then_stops_on_pass(self):
        class FakeMcp:
            def __init__(self):
                self.calls = []
                self.publications = 0

            def start(self): pass
            def stop(self): pass
            def configure(self, context): return {}
            def context_tool(self): return "platform_contract"
            def openai_tools(self):
                names = ("publish_draft", "test_published_bot", "verify_published_bot", "get_saved_draft", "save_draft")
                return [{"type": "function", "function": {"name": name}} for name in names]
            def tool_role(self, name):
                if name == "publish_draft": return "publication"
                if name == "verify_published_bot": return "verification"
                return None
            def call(self, name, arguments):
                self.calls.append((name, dict(arguments)))
                if name == "platform_contract": return {}
                if name == "publish_draft":
                    self.publications += 1
                    return {"published": True, "frontendUrl": f"http://bot/{self.publications}", "test": {"tested": True, "reply": "smoke"}}
                if name == "test_published_bot":
                    reply = "Нет" if self.publications == 1 else "Да"
                    return {"tested": True, "sessionId": f"session-{self.publications}", "reply": reply}
                if name == "get_saved_draft": return {"available": True, "bot": {"name": "draft"}}
                if name == "save_draft": return {"saved": True, "valid": True}
                raise AssertionError(name)

        fake = FakeMcp()
        config = Config("", "", "", "", "", "http://llm.test", "key", "cotype_pro_3", False, None, None, 6, "Hello", Path("debug"), None, Path("skills/mws-nocode"), Path("skills/quality-loop/SKILL.md"))
        agent = Agent(config)
        main_replies = iter([
            tool_response("publish_draft", {}, "main-1"),
            {"choices": [{"message": {"role": "assistant", "content": "I will fix the bot."}}]},
            tool_response("get_saved_draft", {}, "main-2"),
            tool_response("save_draft", {"bot": {"name": "repaired"}}, "main-3"),
            tool_response("publish_draft", {}, "main-4"),
        ])
        qa_runs = iter([
            iter([
                tool_response("qa_set_plan", {"requirements": [{"id": "always_yes", "description": "Always answers Да"}]}),
                tool_response("qa_send_message", {"message": "anything", "requirementIds": ["always_yes"], "purpose": "Check an arbitrary input"}),
                tool_response("qa_finish", {
                    "passed": False, "summary": "The bot answered Нет.",
                    "checks": [{"requirementId": "always_yes", "passed": False, "evidenceTurnIds": [1], "evidence": "Reply was Нет"}],
                    "issues": [{"requirementId": "always_yes", "expected": "Да", "observed": "Нет", "evidenceTurnIds": [1], "repair": "Route every input to an answer block containing Да"}],
                }),
            ]),
            iter([
                tool_response("qa_set_plan", {"requirements": [{"id": "always_yes", "description": "Always answers Да"}]}),
                tool_response("qa_send_message", {"message": "anything", "requirementIds": ["always_yes"], "purpose": "Check an arbitrary input"}),
                tool_response("qa_finish", {
                    "passed": True, "summary": "The repaired bot answers Да.",
                    "checks": [{"requirementId": "always_yes", "passed": True, "evidenceTurnIds": [1], "evidence": "Reply was Да"}],
                    "issues": [],
                }),
            ]),
        ])
        current_qa = None
        main_tool_sets = []
        main_request_options = []
        qa_timeouts = []

        def request(messages, tools, **kwargs):
            nonlocal current_qa
            names = {tool["function"]["name"] for tool in tools}
            if any(name.startswith("qa_") for name in names):
                qa_timeouts.append(kwargs.get("timeout_seconds"))
                if current_qa is None:
                    current_qa = next(qa_runs)
                try:
                    return next(current_qa)
                except StopIteration:
                    current_qa = next(qa_runs)
                    return next(current_qa)
            main_tool_sets.append(names)
            main_request_options.append(kwargs)
            return next(main_replies)

        output = io.StringIO()
        with patch.dict(os.environ, {"MWS_VERIFICATION_MODE": "subagent"}), patch("mws_agent.loop.MCPClient", return_value=fake), patch.object(agent, "llm_request", side_effect=request), redirect_stdout(output):
            agent.run("Создай бота который всегда отвечает Да")

        self.assertTrue(all("test_published_bot" not in names and "verify_published_bot" not in names for names in main_tool_sets))
        self.assertEqual([name for name, _ in fake.calls], ["platform_contract", "publish_draft", "test_published_bot", "get_saved_draft", "save_draft", "publish_draft", "test_published_bot"])
        self.assertIn("QA SUBAGENT found bot defects", output.getvalue())
        self.assertIn("returned text instead of required get_saved_draft", output.getvalue())
        self.assertIn("QA SUBAGENT passed", output.getvalue())
        self.assertIn("Frontend URL: http://bot/2", output.getvalue())
        self.assertTrue(qa_timeouts and all(value == 120 for value in qa_timeouts))
        self.assertEqual(main_tool_sets[1], {"get_saved_draft"})
        self.assertEqual(main_tool_sets[2], {"get_saved_draft"})
        self.assertEqual(main_request_options[1].get("tool_choice"), "required")


if __name__ == "__main__":
    unittest.main()

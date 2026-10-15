import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import orchestrator
from src.models.base import LLMTurn, ToolCall
from src.roles import Role


class ScriptedClient:
    def __init__(self, turns):
        self._turns = list(turns)

    def converse(self, messages, tools, system=None):
        return self._turns.pop(0)


class FakeToolset:
    def __init__(self):
        self.invocations = []

    def schemas(self):
        return [
            {"name": "revisit_over_point", "description": "", "parameters": {}},
            {"name": "ground_track", "description": "", "parameters": {}},
        ]

    def call(self, name, **kwargs):
        self.invocations.append((name, kwargs))
        return {"ok": name}


def _turn(text=None, tool_calls=None):
    return LLMTurn(
        model="fake", input_tokens=1, output_tokens=1, latency_s=0.0,
        text=text, tool_calls=tool_calls or [],
    )


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(
            os.environ, {"USAGE_LOG_PATH": str(Path(self.tmp.name) / "u.jsonl")}
        )
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_single_tool_then_answer(self):
        ts = FakeToolset()
        client = ScriptedClient([
            _turn(tool_calls=[ToolCall("revisit_over_point", {"latitude": 33.4})]),
            _turn(text="about 9 hours"),
        ])
        run = orchestrator.run("revisit over tempe", client, toolset=ts)
        self.assertEqual(run.answer, "about 9 hours")
        self.assertEqual(len(run.steps), 1)
        self.assertEqual(ts.invocations[0][0], "revisit_over_point")
        self.assertFalse(run.truncated)

    def test_multi_call_loop(self):
        ts = FakeToolset()
        client = ScriptedClient([
            _turn(tool_calls=[ToolCall("revisit_over_point", {"n": 1})]),
            _turn(tool_calls=[ToolCall("revisit_over_point", {"n": 2})]),
            _turn(text="done"),
        ])
        run = orchestrator.run("sweep", client, toolset=ts)
        self.assertEqual(len(run.steps), 2)
        self.assertEqual(run.turns, 3)

    def test_cap_truncates(self):
        ts = FakeToolset()
        client = ScriptedClient(
            [_turn(tool_calls=[ToolCall("revisit_over_point", {})]) for _ in range(10)]
        )
        run = orchestrator.run("loop", client, toolset=ts, max_steps=3)
        self.assertTrue(run.truncated)
        self.assertEqual(run.turns, 3)
        self.assertEqual(len(ts.invocations), 3)

    def test_role_tool_gating(self):
        ts = FakeToolset()
        client = ScriptedClient([
            _turn(tool_calls=[ToolCall("revisit_over_point", {})]),
            _turn(text="refused path"),
        ])
        role = Role(name="viewer", tools=("ground_track",))
        run = orchestrator.run("do revisit", client, toolset=ts, role=role)
        self.assertEqual(ts.invocations, [])
        self.assertEqual(run.answer, "refused path")


if __name__ == "__main__":
    unittest.main()

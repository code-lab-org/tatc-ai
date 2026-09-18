import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src import agent
from src.models import LLMResponse
from src.tokens import usage


class FakeLLMClient:
    def __init__(self, text="fake answer"):
        self.text = text
        self.calls = []

    def generate(self, prompt, system=None):
        self.calls.append({"prompt": prompt, "system": system})
        return LLMResponse(
            text=self.text,
            model="fake-model",
            input_tokens=12,
            output_tokens=34,
            latency_s=0.5,
        )


class AssembleContextTests(unittest.TestCase):
    def test_task_alone(self):
        self.assertEqual(agent.assemble_context("do x"), "do x")

    def test_history_precedes_task(self):
        result = agent.assemble_context("do x", history=["earlier note"])
        self.assertEqual(result, "earlier note\n\ndo x")


class RunTaskTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.log_file = Path(self.tmpdir.name) / "usage.jsonl"
        self.env_patcher = mock.patch.dict(
            os.environ, {"USAGE_LOG_PATH": str(self.log_file)}
        )
        self.env_patcher.start()

    def tearDown(self):
        self.env_patcher.stop()
        self.tmpdir.cleanup()

    def test_returns_llm_text_and_passes_system_prompt(self):
        client = FakeLLMClient(text="42")

        result = agent.run_task("meaning of life", client=client)

        self.assertEqual(result, "42")
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["prompt"], "meaning of life")
        self.assertEqual(client.calls[0]["system"], agent.SYSTEM_PROMPT)

    def test_records_usage_entry(self):
        agent.run_task("task", client=FakeLLMClient())

        lines = self.log_file.read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertEqual(entry["task"], "run_agent_task")
        self.assertEqual(entry["model"], "fake-model")
        self.assertEqual(entry["input_tokens"], 12)
        self.assertEqual(entry["output_tokens"], 34)
        self.assertEqual(entry["latency_s"], 0.5)
        self.assertIn("timestamp", entry)

    def test_usage_appends_across_calls(self):
        agent.run_task("first", client=FakeLLMClient())
        agent.run_task("second", client=FakeLLMClient())

        lines = self.log_file.read_text().strip().splitlines()
        self.assertEqual(len(lines), 2)


class UsageLogPathTests(unittest.TestCase):
    def test_defaults_when_env_unset(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("USAGE_LOG_PATH", None)
            self.assertEqual(usage.log_path(), Path("usage.jsonl"))


if __name__ == "__main__":
    unittest.main()

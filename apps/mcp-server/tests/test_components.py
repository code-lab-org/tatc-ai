import unittest

from src.conversation import assemble_context
from src.retrieval import KeywordStore
from src.models import LLMResponse
from src.rendering import render, to_markdown_table
from src.tokens import TokenBudget


class MemoryStoreTests(unittest.TestCase):
    def test_search_ranks_by_term_overlap(self):
        store = KeywordStore()
        store.add("orbit altitude 500 km")
        store.add("ground station in Phoenix")
        store.add("orbit inclination and altitude trade")

        results = store.search("orbit altitude", limit=2)

        self.assertEqual(len(results), 2)
        self.assertIn("orbit altitude 500 km", results)
        self.assertNotIn("ground station in Phoenix", results)

    def test_search_empty_store(self):
        self.assertEqual(KeywordStore().search("anything"), [])


class ContextTests(unittest.TestCase):
    def test_memories_precede_history_and_task(self):
        result = assemble_context("task", history=["h1"], examples=["m1"])
        self.assertEqual(result, "m1\n\nh1\n\ntask")


class RenderingTests(unittest.TestCase):
    def test_json_array_of_objects_becomes_table(self):
        text = '[{"name": "sat-a", "altitude_km": 500}, {"name": "sat-b", "altitude_km": 550}]'

        result = render(text)

        self.assertEqual(
            result,
            "| name | altitude_km |\n| --- | --- |\n| sat-a | 500 |\n| sat-b | 550 |",
        )

    def test_plain_text_passes_through(self):
        self.assertEqual(render("just an answer"), "just an answer")

    def test_json_scalar_passes_through(self):
        self.assertEqual(render("42"), "42")

    def test_table_helper_fills_missing_keys(self):
        table = to_markdown_table([{"a": 1, "b": 2}, {"a": 3}])
        self.assertIn("| 3 |  |", table)


class TokenBudgetTests(unittest.TestCase):
    @staticmethod
    def response(tokens_in, tokens_out):
        return LLMResponse(
            text="", model="m", input_tokens=tokens_in, output_tokens=tokens_out, latency_s=0.1
        )

    def test_unlimited_by_default(self):
        budget = TokenBudget()
        budget.charge(self.response(1000, 1000))
        self.assertFalse(budget.exceeded)

    def test_exceeded_when_over_limit(self):
        budget = TokenBudget(limit=100)
        budget.charge(self.response(60, 30))
        self.assertFalse(budget.exceeded)
        budget.charge(self.response(10, 10))
        self.assertTrue(budget.exceeded)


if __name__ == "__main__":
    unittest.main()

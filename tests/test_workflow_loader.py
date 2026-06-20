import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_compose.workflow_loader import WorkflowLoader


class TestWorkflowLoader(unittest.TestCase):
    def setUp(self):
        self.loader = WorkflowLoader()

    def test_load_basic_workflow(self):
        config = {
            "name": "Article Pipeline",
            "description": "Research and write",
            "entry_task": "research",
            "inputs": ["topic"],
            "outputs": ["final_article"],
            "steps": [
                {
                    "name": "research",
                    "type": "agent",
                    "agent": "web_researcher",
                    "input": "Research topic: ${topic}",
                    "output_key": "research_notes",
                },
                {
                    "name": "write",
                    "type": "agent",
                    "agent": "content_writer",
                    "input": "Write article",
                    "output_key": "draft",
                },
            ],
        }
        result = self.loader.load("article_pipeline", config, "/tmp")
        self.assertEqual(result["name"], "article_pipeline")
        self.assertEqual(result["entry_task"], "research")
        self.assertEqual(len(result["steps"]), 2)
        self.assertEqual(result["steps"][0]["type"], "agent")
        self.assertEqual(result["steps"][0]["agent_ref"], "web_researcher")

    def test_condition_step(self):
        config = {
            "name": "CondWorkflow",
            "steps": [
                {
                    "name": "qc",
                    "type": "condition",
                    "evaluator": "my_module:my_function",
                    "cases": {
                        "pass": [{"name": "write", "type": "agent", "agent": "writer"}],
                        "fail": [{"name": "retry", "type": "agent", "agent": "researcher"}],
                    },
                    "default": [],
                },
            ],
        }
        result = self.loader.load("cond", config, "/tmp")
        step = result["steps"][0]
        self.assertEqual(step["type"], "condition")
        self.assertEqual(step["evaluator_ref"], "my_module:my_function")
        self.assertIn("pass", step["cases"])
        self.assertIn("fail", step["cases"])
        self.assertEqual(len(step["cases"]["pass"]), 1)

    def test_parallel_step(self):
        config = {
            "name": "ParallelWf",
            "steps": [
                {
                    "name": "parallel_branch",
                    "type": "parallel",
                    "branches": [
                        {"name": "b1", "steps": [{"name": "s1", "type": "agent", "agent": "a1"}]},
                        {"name": "b2", "steps": [{"name": "s2", "type": "agent", "agent": "a2"}]},
                    ],
                },
            ],
        }
        result = self.loader.load("par", config, "/tmp")
        step = result["steps"][0]
        self.assertEqual(step["type"], "parallel")
        self.assertEqual(len(step["branches"]), 2)

    def test_function_step(self):
        config = {
            "name": "FuncWf",
            "steps": [
                {
                    "name": "publish",
                    "type": "function",
                    "function": "my_module:publish_fn",
                    "args": {"title": "Article"},
                },
            ],
        }
        result = self.loader.load("fn", config, "/tmp")
        step = result["steps"][0]
        self.assertEqual(step["type"], "function")
        self.assertEqual(step["function_ref"], "my_module:publish_fn")

    def test_caching(self):
        config = {"name": "Cache", "steps": []}
        first = self.loader.load("cache_test", config, "/tmp")
        second = self.loader.load("cache_test", config, "/tmp")
        self.assertIs(first, second)

    def test_resolve_function_none_for_invalid(self):
        self.assertIsNone(self.loader._resolve_function(""))
        self.assertIsNone(self.loader._resolve_function(None))


if __name__ == "__main__":
    unittest.main()

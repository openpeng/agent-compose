import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_compose.agent_loader import AgentLoader


class TestAgentLoader(unittest.TestCase):
    def setUp(self):
        self.loader = AgentLoader()

    def test_load_basic_agent(self):
        config = {
            "role": "Researcher",
            "description": "Research agent",
            "instructions": ["Be helpful", "Use tools"],
            "model": {"provider": "openai", "id": "gpt-4o"},
            "tools": {
                "builtin": ["web_search", "calculator"],
            },
            "markdown": True,
        }
        result = self.loader.load("researcher", config, "/tmp")
        self.assertEqual(result["name"], "researcher")
        self.assertEqual(result["role"], "Researcher")
        self.assertEqual(len(result["instructions"]), 2)
        self.assertEqual(result["model"]["provider"], "openai")
        self.assertTrue(result["markdown"])

    def test_build_model(self):
        model = self.loader._build_model({"provider": "anthropic", "id": "claude-sonnet-4"})
        self.assertEqual(model["provider"], "anthropic")
        self.assertEqual(model["id"], "claude-sonnet-4")
        self.assertEqual(model["module"], "agno.models.anthropic")

    def test_build_tools(self):
        tools = self.loader._build_tools(
            [{"name": "web_search", "provider": "ddg"}, "calculator"],
            "/tmp",
        )
        self.assertEqual(len(tools), 2)
        self.assertEqual(tools[0]["type"], "builtin")
        self.assertEqual(tools[1]["type"], "builtin")

    def test_ensure_list_string(self):
        result = self.loader._ensure_list("single instruction")
        self.assertEqual(result, ["single instruction"])

    def test_ensure_list_list(self):
        result = self.loader._ensure_list(["a", "b", "c"])
        self.assertEqual(result, ["a", "b", "c"])

    def test_memory_build(self):
        memory = self.loader._build_memory({"type": "short_term", "table_name": "agent_mem"})
        self.assertEqual(memory["type"], "short_term")
        self.assertEqual(memory["table_name"], "agent_mem")

    def test_agent_caching(self):
        config = {"role": "Cacher", "description": "Test"}
        first = self.loader.load("cacher", config, "/tmp")
        second = self.loader.load("cacher", config, "/tmp")
        self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()

import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_compose.mcp_builder import MCPBuilder


class TestMCPBuilder(unittest.TestCase):
    def setUp(self):
        self.builder = MCPBuilder()

    def test_build_stdio(self):
        cfg = {"type": "stdio", "name": "FS", "command": "npx", "args": ["-y", "tool"]}
        result = self.builder.build(cfg)
        self.assertEqual(result["type"], "stdio")
        self.assertEqual(result["name"], "FS")
        self.assertEqual(result["command"], "npx")
        self.assertEqual(result["args"], ["-y", "tool"])

    def test_build_sse(self):
        cfg = {"type": "sse", "name": "Search", "url": "https://example.com", "auth_token": "abc"}
        result = self.builder.build(cfg)
        self.assertEqual(result["type"], "sse")
        self.assertEqual(result["url"], "https://example.com")
        self.assertEqual(result["auth_token"], "abc")

    def test_build_multi(self):
        cfg = {
            "type": "multi",
            "name": "Combo",
            "servers": [
                {"type": "stdio", "name": "A", "command": "cmd_a"},
                {"type": "sse", "name": "B", "url": "http://b"},
            ],
        }
        result = self.builder.build(cfg)
        self.assertEqual(result["type"], "multi")
        self.assertEqual(len(result["servers"]), 2)
        self.assertEqual(result["servers"][0]["type"], "stdio")
        self.assertEqual(result["servers"][1]["type"], "sse")

    def test_invalid_type_raises(self):
        with self.assertRaises(ValueError):
            self.builder.build({"type": "unknown", "name": "X"})

    def test_build_all(self):
        configs = [
            {"type": "stdio", "name": "A", "command": "cmd"},
            {"type": "sse", "name": "B", "url": "http://b"},
        ]
        results = self.builder.build_all(configs)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["type"], "stdio")
        self.assertEqual(results[1]["type"], "sse")


if __name__ == "__main__":
    unittest.main()

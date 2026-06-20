import unittest
import tempfile
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_compose.config_resolver import ConfigResolver


class TestConfigResolver(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ac_test_res_"))
        self.base = str(self.tmp)

    def write_file(self, rel_path: str, content: str):
        p = self.tmp / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return str(p)

    def test_resolve_simple_dict(self):
        resolver = ConfigResolver()
        result = resolver.resolve({"role": "Researcher", "markdown": True}, self.base)
        self.assertEqual(result["role"], "Researcher")
        self.assertTrue(result["markdown"])

    def test_resolve_ref(self):
        self.write_file("definitions/mcp_ref.yml", """
mcps:
  my_mcp:
    name: "TestMCP"
    type: stdio
    command: "test"
""")
        resolver = ConfigResolver(str(self.tmp / "definitions"))
        config = {"my_ref": {"$ref": "mcp_ref.yml::mcps.my_mcp"}}
        result = resolver.resolve(config, self.base)
        self.assertIn("my_ref", result)

    def test_resolve_file_string(self):
        self.write_file("prompts/instructions.txt", "Use the tools wisely.")
        resolver = ConfigResolver()
        config = {"instructions": {"$file": "prompts/instructions.txt"}}
        result = resolver.resolve(config, self.base)
        self.assertEqual(result["instructions"], "Use the tools wisely.")

    def test_substitute_env_vars(self):
        os.environ["AC_TEST_VAR"] = "hello"
        resolver = ConfigResolver()
        result = resolver._substitute_env_vars("Value is ${AC_TEST_VAR}")
        self.assertEqual(result, "Value is hello")

    def test_substitute_env_default(self):
        resolver = ConfigResolver()
        result = resolver._substitute_env_vars("${UNSET_VAR:-fallback}")
        self.assertEqual(result, "fallback")

    def test_deep_merge(self):
        base = {"a": 1, "b": {"c": 2}}
        override = {"b": {"d": 3}, "e": 4}
        merged = ConfigResolver.deep_merge(base, override)
        self.assertEqual(merged["a"], 1)
        self.assertEqual(merged["b"]["c"], 2)
        self.assertEqual(merged["b"]["d"], 3)
        self.assertEqual(merged["e"], 4)

    def test_resolve_list(self):
        resolver = ConfigResolver()
        result = resolver.resolve({"items": [1, 2, {"nested": True}]}, self.base)
        self.assertEqual(len(result["items"]), 3)
        self.assertTrue(result["items"][2]["nested"])


if __name__ == "__main__":
    unittest.main()

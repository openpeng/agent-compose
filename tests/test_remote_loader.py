import unittest
import tempfile
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_compose.remote_loader import RemoteAgentLoader


class TestRemoteAgentLoader(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ac_test_remote_"))
        self.loader = RemoteAgentLoader(cache_dir=str(self.tmp / "cache"))

    def test_parse_market_ref(self):
        name, version = self.loader._parse_market_ref("market://my_agent")
        self.assertEqual(name, "my_agent")
        self.assertIsNone(version)

    def test_parse_market_ref_with_version(self):
        name, version = self.loader._parse_market_ref("market://my_agent@1.2.3")
        self.assertEqual(name, "my_agent")
        self.assertEqual(version, "1.2.3")

    def test_file_ref_resolution(self):
        agent_file = self.tmp / "my_agent.json"
        agent_file.write_text(
            json.dumps({"name": "TestAgent", "role": "Tester"}),
            encoding="utf-8",
        )
        result = self.loader.resolve(f"file://{agent_file}")
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "TestAgent")

    def test_resolve_none(self):
        self.assertIsNone(self.loader.resolve(""))
        self.assertIsNone(self.loader.resolve(None))

    def test_url_falls_back_gracefully(self):
        result = self.loader.resolve("http://invalid.example.com/agent.json")
        self.assertIsNone(result)

    def test_market_falls_back_gracefully(self):
        result = self.loader.resolve("market://non_existent_agent")
        self.assertIsNone(result)

    def test_hash_is_consistent(self):
        h1 = self.loader._hash("some_string")
        h2 = self.loader._hash("some_string")
        self.assertEqual(h1, h2)
        h3 = self.loader._hash("different_string")
        self.assertNotEqual(h1, h3)

    def test_cache_dir_created(self):
        self.assertTrue(self.loader.cache_dir.exists())


if __name__ == "__main__":
    unittest.main()

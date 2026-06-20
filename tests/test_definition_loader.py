import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_compose.definition_loader import DefinitionLoader


class TestDefinitionLoader(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="ac_test_def_"))
        (self.tmp / "definitions").mkdir(parents=True, exist_ok=True)

    def write_file(self, name: str, content: str):
        p = self.tmp / "definitions" / name
        p.write_text(content, encoding="utf-8")
        return p

    def test_load_skills(self):
        self.write_file("skills.yml", """
skills:
  data_analyzer:
    name: "DataAnalyzer"
    description: "Analyze structured data"
""")
        loader = DefinitionLoader(str(self.tmp / "definitions"))
        loader.load()
        self.assertEqual(loader.list_skills(), ["data_analyzer"])
        skill = loader.get_skill("data_analyzer")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.get("name"), "DataAnalyzer")

    def test_load_mcps(self):
        self.write_file("skills.yml", "skills: {}")
        self.write_file("mcps.yml", """
mcps:
  filesystem:
    name: "FileSystem"
    type: stdio
    command: npx
""")
        loader = DefinitionLoader(str(self.tmp / "definitions"))
        loader.load()
        self.assertEqual(loader.list_mcps(), ["filesystem"])
        mcp = loader.get_mcp("filesystem")
        self.assertEqual(mcp.get("type"), "stdio")

    def test_load_agent_templates(self):
        self.write_file("skills.yml", "skills: {}")
        self.write_file("mcps.yml", "mcps: {}")
        self.write_file("agents.yml", """
agents:
  base_researcher:
    role: "Researcher"
    description: "Base researcher template"
""")
        loader = DefinitionLoader(str(self.tmp / "definitions"))
        loader.load()
        self.assertIn("base_researcher", loader.list_agent_templates())
        template = loader.get_agent_template("base_researcher")
        self.assertEqual(template.get("role"), "Researcher")

    def test_missing_file_returns_none(self):
        loader = DefinitionLoader(str(self.tmp / "definitions"))
        loader.load()
        self.assertIsNone(loader.get_skill("nonexistent"))
        self.assertIsNone(loader.get_mcp("nonexistent"))
        self.assertIsNone(loader.get_agent_template("nonexistent"))

    def test_load_is_idempotent(self):
        self.write_file("skills.yml", "skills: {}\n")
        loader = DefinitionLoader(str(self.tmp / "definitions"))
        loader.load()
        loader.load()
        loader.load()
        self.assertEqual(loader.list_skills(), [])


if __name__ == "__main__":
    unittest.main()

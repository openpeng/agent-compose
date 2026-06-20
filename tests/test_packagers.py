import unittest
import tempfile
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_compose.packagers.agent_packager import AgentPackager
from agent_compose.packagers.team_packager import TeamPackager
from agent_compose.packagers.workflow_packager import WorkflowPackager


class TestAgentPackager(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ac_test_agentpkg_"))
        self.pkg = AgentPackager(output_dir=str(self.tmp))

    def test_package_creates_json(self):
        agent_config = {
            "name": "test_agent",
            "role": "Tester",
            "description": "Test agent",
            "instructions": ["Instruction 1", "Instruction 2"],
            "tools": [{"name": "WebSearch", "type": "builtin"}],
            "model": {"provider": "openai", "id": "gpt-4o"},
        }
        path = self.pkg.package("test_agent", agent_config)
        json_path = Path(path) / "agent.json"
        self.assertTrue(json_path.exists())
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["type"], "agent")
        self.assertEqual(data["name"], "test_agent")
        self.assertEqual(data["schema_version"], "1.0.0")
        self.assertIn("tool:WebSearch", data["capabilities"])

    def test_package_with_deploy_config(self):
        agent_config = {"name": "agent", "role": "Role", "instructions": []}
        deploy = {
            "version": "2.0.0",
            "author": "me@example.com",
            "category": "research",
            "tags": ["tag1", "tag2"],
            "targets": ["cursor"],
        }
        path = self.pkg.package("my_agent", agent_config, raw_config={"deploy": deploy})
        json_path = Path(path) / "agent.json"
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["version"], "2.0.0")
        self.assertEqual(data["author"], "me@example.com")
        self.assertEqual(data["category"], "research")


class TestTeamPackager(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ac_test_teampkg_"))
        self.pkg = TeamPackager(output_dir=str(self.tmp))

    def test_package_creates_json(self):
        team_config = {
            "name": "content_team",
            "mode": "coordinate",
            "description": "Team for content",
            "instructions": ["Do good work"],
            "members": [
                {"name": "researcher", "role": "Researcher"},
                {"name": "writer", "role": "Writer"},
            ],
            "shared_state": True,
        }
        path = self.pkg.package("content_team", team_config)
        json_path = Path(path) / "team.json"
        self.assertTrue(json_path.exists())
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["type"], "team")
        self.assertEqual(data["mode"], "coordinate")
        self.assertEqual(len(data["agents"]), 2)
        self.assertEqual(data["dependencies"]["agents"], 2)


class TestWorkflowPackager(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ac_test_wfpkg_"))
        self.pkg = WorkflowPackager(output_dir=str(self.tmp))

    def test_package_creates_json(self):
        workflow_config = {
            "name": "Article Pipeline",
            "description": "Research and write",
            "entry_task": "research",
            "inputs": ["topic"],
            "outputs": ["final_article"],
            "steps": [
                {"name": "research", "type": "agent", "agent": "web_researcher", "output_key": "notes"},
                {"name": "write", "type": "agent", "agent": "content_writer", "output_key": "draft"},
            ],
        }
        path = self.pkg.package("article_pipeline", workflow_config)
        json_path = Path(path) / "workflow.json"
        self.assertTrue(json_path.exists())
        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["type"], "workflow")
        self.assertEqual(len(data["steps"]), 2)
        self.assertIn("web_researcher", data["dependencies"]["agents"])
        self.assertIn("content_writer", data["dependencies"]["agents"])


if __name__ == "__main__":
    unittest.main()

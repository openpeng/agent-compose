import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_compose.team_loader import TeamLoader


class TestTeamLoader(unittest.TestCase):
    def setUp(self):
        self.loader = TeamLoader()

    def test_load_team_basic(self):
        config = {
            "name": "ContentTeam",
            "mode": "coordinate",
            "description": "Content team",
            "instructions": ["Do good work"],
            "shared_state": True,
            "agents": [
                {"name": "researcher", "role": "Researcher"},
                "writer",
            ],
        }
        result = self.loader.load("content_team", config, "/tmp")
        self.assertEqual(result["name"], "content_team")
        self.assertEqual(result["mode"], "coordinate")
        self.assertEqual(len(result["members"]), 2)
        self.assertTrue(result["shared_state"])

    def test_default_mode(self):
        config = {"name": "T", "agents": ["a"]}
        result = self.loader.load("t", config, "/tmp")
        self.assertEqual(result["mode"], "collaborate")

    def test_leader_building(self):
        config = {
            "name": "LT",
            "mode": "coordinate",
            "leader": {
                "name": "team_lead",
                "role": "Team Leader",
                "description": "Lead the team",
            },
            "agents": ["a", "b"],
        }
        result = self.loader.load("lt", config, "/tmp")
        self.assertIsNotNone(result["leader"])
        self.assertEqual(result["leader"]["name"], "team_lead")

    def test_member_names(self):
        config = {
            "name": "MT",
            "agents": [
                {"name": "alice", "role": "Alice"},
                {"name": "bob", "role": "Bob"},
                "charlie",
            ],
        }
        result = self.loader.load("mt", config, "/tmp")
        names = [m.get("name") for m in result["members"]]
        self.assertEqual(names, ["alice", "bob", "charlie"])


if __name__ == "__main__":
    unittest.main()

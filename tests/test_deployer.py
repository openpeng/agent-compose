import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_compose.deployer import Deployer


class TestDeployer(unittest.TestCase):
    def setUp(self):
        self.deployer = Deployer()

    def test_deploy_agent(self):
        result = self.deployer.register_agent("test", {"role": "Tester"})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["deployed"]["type"], "agent")
        self.assertEqual(result["deployed"]["name"], "test")

    def test_deploy_team(self):
        result = self.deployer.register_team("my_team", {"mode": "coordinate"})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["deployed"]["type"], "team")

    def test_deploy_workflow(self):
        result = self.deployer.register_workflow("wf1", {"steps": []})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["deployed"]["type"], "workflow")

    def test_invalid_type(self):
        result = self.deployer.deploy_to_agentos("invalid", "x", {})
        self.assertEqual(result["status"], "error")

    def test_deploy_to_agentos(self):
        result = self.deployer.deploy_to_agentos("agent", "t", {"role": "R"})
        self.assertEqual(result["status"], "ok")
        self.assertIn("deploy_url", result["deployed"])


if __name__ == "__main__":
    unittest.main()

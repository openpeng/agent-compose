import io
import json
import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_compose.cli import main as cli_main, build_parser


def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestCLI(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ac_test_cli_"))
        (self.tmp / "configs").mkdir(parents=True, exist_ok=True)

    def test_parser_builds(self):
        parser = build_parser()
        self.assertIsNotNone(parser)

    def test_list_empty(self):
        argv = ["-d", str(self.tmp), "list"]
        exit_code = cli_main(argv)
        self.assertEqual(exit_code, 0)

    def test_list_agents_kind(self):
        argv = ["-d", str(self.tmp), "list", "agents"]
        exit_code = cli_main(argv)
        self.assertEqual(exit_code, 0)

    def test_agent_not_found(self):
        argv = ["-d", str(self.tmp), "agent", "nonexistent"]
        exit_code = cli_main(argv)
        self.assertEqual(exit_code, 1)

    def test_agent_found(self):
        write_file(
            self.tmp / "configs" / "agents.yml",
            """
agents:
  helper:
    role: "Helper"
    description: "Helpful agent"
    instructions:
      - "Assist"
    model:
      provider: openai
      id: gpt-4o
""",
        )
        argv = ["-d", str(self.tmp), "agent", "helper"]
        exit_code = cli_main(argv)
        self.assertEqual(exit_code, 0)

    def test_agent_json_output(self):
        write_file(
            self.tmp / "configs" / "agents.yml",
            """
agents:
  researcher:
    role: "Researcher"
    description: "Research"
    instructions:
      - "Research well"
    model:
      provider: openai
      id: gpt-4o
""",
        )
        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf
        try:
            exit_code = cli_main(["-d", str(self.tmp), "agent", "researcher", "--json"])
        finally:
            sys.stdout = old_stdout
        self.assertEqual(exit_code, 0)
        output = buf.getvalue()
        data = json.loads(output)
        self.assertEqual(data["role"], "Researcher")

    def test_team_not_found(self):
        argv = ["-d", str(self.tmp), "team", "nonexistent"]
        exit_code = cli_main(argv)
        self.assertEqual(exit_code, 1)

    def test_team_found(self):
        write_file(
            self.tmp / "configs" / "teams.yml",
            """
teams:
  my_team:
    name: "MyTeam"
    mode: coordinate
    description: "Team"
    agents:
      - agent1
      - agent2
""",
        )
        argv = ["-d", str(self.tmp), "team", "my_team"]
        exit_code = cli_main(argv)
        self.assertEqual(exit_code, 0)

    def test_workflow_not_found(self):
        argv = ["-d", str(self.tmp), "workflow", "nonexistent"]
        exit_code = cli_main(argv)
        self.assertEqual(exit_code, 1)

    def test_workflow_found(self):
        write_file(
            self.tmp / "configs" / "workflows.yml",
            """
workflows:
  my_wf:
    name: "MyWorkflow"
    description: "Test workflow"
    steps:
      - name: step1
        type: agent
        agent: worker
        output_key: out
""",
        )
        argv = ["-d", str(self.tmp), "workflow", "my_wf"]
        exit_code = cli_main(argv)
        self.assertEqual(exit_code, 0)

    def test_run_workflow(self):
        write_file(
            self.tmp / "configs" / "workflows.yml",
            """
workflows:
  pipe:
    name: "Pipeline"
    steps:
      - name: s1
        type: agent
        agent: a1
        output_key: k1
""",
        )
        exit_code = cli_main(["-d", str(self.tmp), "run", "pipe"])
        self.assertEqual(exit_code, 0)

    def test_load_all(self):
        write_file(
            self.tmp / "configs" / "agents.yml",
            """
agents:
  helper:
    role: "Helper"
    description: "Test"
    instructions:
      - "Help"
    model:
      provider: openai
      id: gpt-4o
""",
        )
        exit_code = cli_main(["-d", str(self.tmp), "load-all"])
        self.assertEqual(exit_code, 0)

    def test_package_agent(self):
        write_file(
            self.tmp / "configs" / "agents.yml",
            """
agents:
  packager_test:
    role: "Tester"
    description: "Pack test"
    instructions:
      - "Test"
    model:
      provider: openai
      id: gpt-4o
""",
        )
        output_dir = self.tmp / "out"
        exit_code = cli_main(
            ["-d", str(self.tmp), "-o", str(output_dir), "package", "agent", "packager_test"]
        )
        self.assertEqual(exit_code, 0)

    def test_package_team(self):
        write_file(
            self.tmp / "configs" / "teams.yml",
            """
teams:
  team_pkg:
    name: "Team"
    mode: coordinate
    description: "Team for pkg"
    agents:
      - a1
      - a2
""",
        )
        output_dir = self.tmp / "out2"
        exit_code = cli_main(
            ["-d", str(self.tmp), "-o", str(output_dir), "package", "team", "team_pkg"]
        )
        self.assertEqual(exit_code, 0)

    def test_package_workflow(self):
        write_file(
            self.tmp / "configs" / "workflows.yml",
            """
workflows:
  wf_pkg:
    name: "Workflow"
    description: "WF for pkg"
    steps:
      - name: s1
        type: agent
        agent: a1
        output_key: k1
""",
        )
        output_dir = self.tmp / "out3"
        exit_code = cli_main(
            ["-d", str(self.tmp), "-o", str(output_dir), "package", "workflow", "wf_pkg"]
        )
        self.assertEqual(exit_code, 0)

    def test_package_not_found(self):
        exit_code = cli_main(["-d", str(self.tmp), "package", "agent", "nonexistent"])
        self.assertEqual(exit_code, 1)

    def test_deploy_agent(self):
        write_file(
            self.tmp / "configs" / "agents.yml",
            """
agents:
  deploy_test:
    role: "Deployer"
    description: "Deploy test"
    instructions:
      - "Deploy"
    model:
      provider: openai
      id: gpt-4o
""",
        )
        exit_code = cli_main(["-d", str(self.tmp), "deploy", "agent", "deploy_test"])
        self.assertEqual(exit_code, 0)

    def test_deploy_team(self):
        write_file(
            self.tmp / "configs" / "teams.yml",
            """
teams:
  deploy_team:
    name: "DeployTeam"
    mode: coordinate
    description: "Deploy team"
    agents:
      - a1
""",
        )
        exit_code = cli_main(["-d", str(self.tmp), "deploy", "team", "deploy_team"])
        self.assertEqual(exit_code, 0)

    def test_deploy_workflow(self):
        write_file(
            self.tmp / "configs" / "workflows.yml",
            """
workflows:
  deploy_wf:
    name: "DeployWF"
    description: "Deploy workflow"
    steps:
      - name: s1
        type: agent
        agent: a1
        output_key: k1
""",
        )
        exit_code = cli_main(["-d", str(self.tmp), "deploy", "workflow", "deploy_wf"])
        self.assertEqual(exit_code, 0)

    def test_deploy_not_found(self):
        exit_code = cli_main(["-d", str(self.tmp), "deploy", "agent", "nonexistent"])
        self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()

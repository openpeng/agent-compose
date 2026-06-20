import unittest
import tempfile
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent_compose.orchestrator import YamlOrchestrator


def write_file(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestOrchestrator(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ac_test_orch_"))
        (self.tmp / "configs").mkdir(parents=True, exist_ok=True)
        (self.tmp / "definitions").mkdir(parents=True, exist_ok=True)

    def test_load_all_empty(self):
        orch = YamlOrchestrator(project_dir=str(self.tmp))
        data = orch.load_all()
        self.assertEqual(data["agents"], {})
        self.assertEqual(data["teams"], {})
        self.assertEqual(data["workflows"], {})

    def test_list_empty(self):
        orch = YamlOrchestrator(project_dir=str(self.tmp))
        self.assertEqual(orch.list_agents(), [])
        self.assertEqual(orch.list_teams(), [])
        self.assertEqual(orch.list_workflows(), [])

    def test_get_agent(self):
        write_file(
            self.tmp / "configs" / "agents.yml",
            """
agents:
  researcher:
    role: "Researcher"
    description: "Do research"
    instructions:
      - "Be helpful"
    model:
      provider: openai
      id: gpt-4o
    markdown: true
""",
        )
        orch = YamlOrchestrator(project_dir=str(self.tmp))
        agent = orch.get_agent("researcher")
        self.assertIsNotNone(agent)
        self.assertEqual(agent["role"], "Researcher")

    def test_get_team(self):
        write_file(
            self.tmp / "configs" / "teams.yml",
            """
teams:
  content_team:
    name: "ContentTeam"
    mode: coordinate
    description: "Content team"
    agents:
      - researcher
      - writer
""",
        )
        orch = YamlOrchestrator(project_dir=str(self.tmp))
        team = orch.get_team("content_team")
        self.assertIsNotNone(team)
        self.assertEqual(team["mode"], "coordinate")

    def test_get_workflow(self):
        write_file(
            self.tmp / "configs" / "workflows.yml",
            """
workflows:
  article_pipeline:
    name: "Article Pipeline"
    description: "Research and write"
    entry_task: research
    steps:
      - name: research
        type: agent
        agent: web_researcher
        input: "Research topic"
        output_key: notes
      - name: write
        type: agent
        agent: content_writer
        input: "Write article"
        output_key: article
""",
        )
        orch = YamlOrchestrator(project_dir=str(self.tmp))
        wf = orch.get_workflow("article_pipeline")
        self.assertIsNotNone(wf)
        self.assertEqual(len(wf["steps"]), 2)

    def test_agent_not_found(self):
        orch = YamlOrchestrator(project_dir=str(self.tmp))
        self.assertIsNone(orch.get_agent("nonexistent"))
        self.assertIsNone(orch.get_team("nonexistent"))
        self.assertIsNone(orch.get_workflow("nonexistent"))

    def test_run_workflow_success(self):
        write_file(
            self.tmp / "configs" / "workflows.yml",
            """
workflows:
  simple_pipeline:
    name: "Simple"
    steps:
      - name: step1
        type: agent
        agent: worker
        output_key: out
""",
        )
        orch = YamlOrchestrator(project_dir=str(self.tmp))
        result = orch.run_workflow("simple_pipeline")
        self.assertEqual(result["status"], "ok")

    def test_run_workflow_missing(self):
        orch = YamlOrchestrator(project_dir=str(self.tmp))
        result = orch.run_workflow("missing")
        self.assertEqual(result["status"], "error")

    def test_to_json(self):
        write_file(
            self.tmp / "configs" / "agents.yml",
            """
agents:
  helper:
    role: "Helper"
    description: "Helpful"
    model:
      provider: openai
      id: gpt-4o
""",
        )
        orch = YamlOrchestrator(project_dir=str(self.tmp))
        json_str = orch.to_json()
        data = json.loads(json_str)
        self.assertIn("agents", data)
        self.assertIn("helper", data["agents"])

    def test_full_integration(self):
        write_file(
            self.tmp / "configs" / "agents.yml",
            """
agents:
  researcher:
    role: "Researcher"
    description: "Research the web"
    instructions:
      - "Use web search"
    tools:
      builtin:
        - web_search
    model:
      provider: openai
      id: gpt-4o
    markdown: true

  writer:
    role: "Writer"
    description: "Write content"
    instructions:
      - "Write clear content"
    model:
      provider: anthropic
      id: claude-sonnet-4
""",
        )
        write_file(
            self.tmp / "configs" / "teams.yml",
            """
teams:
  content_team:
    name: "ContentTeam"
    mode: coordinate
    description: "Research and write"
    agents:
      - name: researcher
        role: "Researcher"
      - name: writer
        role: "Writer"
""",
        )
        write_file(
            self.tmp / "configs" / "workflows.yml",
            """
workflows:
  article_pipeline:
    name: "Article Pipeline"
    description: "Full article pipeline"
    steps:
      - name: research
        type: agent
        agent: researcher
        input: "Research the topic"
        output_key: notes
      - name: write
        type: agent
        agent: writer
        input: "Write from notes"
        output_key: article
""",
        )
        orch = YamlOrchestrator(project_dir=str(self.tmp))
        all_data = orch.load_all()
        self.assertIn("researcher", all_data["agents"])
        self.assertIn("writer", all_data["agents"])
        self.assertIn("content_team", all_data["teams"])
        self.assertIn("article_pipeline", all_data["workflows"])


if __name__ == "__main__":
    unittest.main()

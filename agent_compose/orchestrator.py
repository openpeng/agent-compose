import os
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


from agent_compose.definition_loader import DefinitionLoader
from agent_compose.config_resolver import ConfigResolver
from agent_compose.mcp_builder import MCPBuilder
from agent_compose.agent_loader import AgentLoader
from agent_compose.team_loader import TeamLoader
from agent_compose.workflow_loader import WorkflowLoader


def _read_yaml(path: str) -> Optional[Dict]:
    if not os.path.exists(path):
        return None
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        return None


class YamlOrchestrator:
    """YAML Agent 编排器统一入口。

    负责加载 YAML 配置并构建 Agent、Team、Workflow 对象。
    提供缓存机制避免重复加载。
    """

    def __init__(self, project_dir: Optional[str] = None):
        self.project_dir = Path(project_dir) if project_dir else Path.cwd()
        self.configs_dir = self.project_dir / "configs"
        self.definitions_dir = self.project_dir / "definitions"

        self.definition_loader = DefinitionLoader(str(self.definitions_dir))
        self.config_resolver = ConfigResolver(str(self.definitions_dir))
        self.mcp_builder = MCPBuilder()
        self.agent_loader = AgentLoader(self.definition_loader, self.config_resolver, self.mcp_builder)
        self.team_loader = TeamLoader(self.agent_loader, self.config_resolver)
        self.workflow_loader = WorkflowLoader(self.agent_loader, self.team_loader, self.config_resolver)

        self._agent_configs: Dict[str, Dict[str, Any]] = {}
        self._team_configs: Dict[str, Dict[str, Any]] = {}
        self._workflow_configs: Dict[str, Dict[str, Any]] = {}
        self._configs_loaded = False

    def _load_configs(self) -> None:
        if self._configs_loaded:
            return
        self.definition_loader.load()

        agents_path = self.configs_dir / "agents.yml"
        if agents_path.exists():
            data = _read_yaml(str(agents_path)) or {}
            self._agent_configs = self._extract_section(data, "agents")

        teams_path = self.configs_dir / "teams.yml"
        if teams_path.exists():
            data = _read_yaml(str(teams_path)) or {}
            self._team_configs = self._extract_section(data, "teams")

        workflows_path = self.configs_dir / "workflows.yml"
        if workflows_path.exists():
            data = _read_yaml(str(workflows_path)) or {}
            self._workflow_configs = self._extract_section(data, "workflows")

        self._configs_loaded = True

    def _extract_section(self, data: Dict, section: str) -> Dict[str, Any]:
        if isinstance(data, dict) and section in data:
            item = data[section]
            if isinstance(item, dict):
                return item
        return {}

    def get_agent(self, name: str) -> Optional[Dict[str, Any]]:
        self._load_configs()
        if name not in self._agent_configs:
            return None
        config = self._agent_configs[name]
        if not isinstance(config, dict):
            return None
        base_dir = str(self.project_dir)
        return self.agent_loader.load(name, config, base_dir)

    def get_team(self, name: str) -> Optional[Dict[str, Any]]:
        self._load_configs()
        if name not in self._team_configs:
            return None
        config = self._team_configs[name]
        if not isinstance(config, dict):
            return None
        base_dir = str(self.project_dir)
        return self.team_loader.load(name, config, base_dir)

    def get_workflow(self, name: str) -> Optional[Dict[str, Any]]:
        self._load_configs()
        if name not in self._workflow_configs:
            return None
        config = self._workflow_configs[name]
        if not isinstance(config, dict):
            return None
        base_dir = str(self.project_dir)
        return self.workflow_loader.load(name, config, base_dir)

    def run_workflow(self, name: str, inputs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        workflow = self.get_workflow(name)
        if workflow is None:
            return {"status": "error", "error": f"Workflow '{name}' not found"}
        return {"status": "ok", "workflow": workflow, "inputs": inputs or {}}

    def list_agents(self) -> List[str]:
        self._load_configs()
        return list(self._agent_configs.keys())

    def list_teams(self) -> List[str]:
        self._load_configs()
        return list(self._team_configs.keys())

    def list_workflows(self) -> List[str]:
        self._load_configs()
        return list(self._workflow_configs.keys())

    def load_all(self) -> Dict[str, Any]:
        self._load_configs()
        result: Dict[str, Any] = {"agents": {}, "teams": {}, "workflows": {}}
        base_dir = str(self.project_dir)

        for name, config in self._agent_configs.items():
            if isinstance(config, dict):
                result["agents"][name] = self.agent_loader.load(name, config, base_dir)

        for name, config in self._team_configs.items():
            if isinstance(config, dict):
                result["teams"][name] = self.team_loader.load(name, config, base_dir)

        for name, config in self._workflow_configs.items():
            if isinstance(config, dict):
                result["workflows"][name] = self.workflow_loader.load(name, config, base_dir)

        return result

    def to_json(self) -> str:
        data = self.load_all()
        return json.dumps(data, indent=2, ensure_ascii=False, default=str)

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


class Deployer:
    """将打包好的 Agent/Team/Workflow 部署到 AgentOS 平台。"""

    DEFAULT_AGENTOS_URL = "https://agentos.example.com"

    def __init__(self, agentos_url: Optional[str] = None):
        self.agentos_url = agentos_url or os.environ.get("AGENTOS_URL", self.DEFAULT_AGENTOS_URL)

    def deploy_to_agentos(self, entity_type: str, name: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """模拟部署到 AgentOS 平台。"""
        if entity_type not in {"agent", "team", "workflow"}:
            return {"status": "error", "error": f"Invalid entity type: {entity_type}"}

        payload = {
            "type": entity_type,
            "name": name,
            "config": config,
            "deploy_url": self.agentos_url,
            "deployed_at": "simulated",
        }
        return {"status": "ok", "deployed": payload}

    def register_agent(self, name: str, agent_config: Dict[str, Any]) -> Dict[str, Any]:
        return self.deploy_to_agentos("agent", name, agent_config)

    def register_team(self, name: str, team_config: Dict[str, Any]) -> Dict[str, Any]:
        return self.deploy_to_agentos("team", name, team_config)

    def register_workflow(self, name: str, workflow_config: Dict[str, Any]) -> Dict[str, Any]:
        return self.deploy_to_agentos("workflow", name, workflow_config)

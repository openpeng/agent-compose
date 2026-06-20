import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


class TeamPackager:
    """将 Team 配置打包为标准 team.json 格式。"""

    def __init__(self, output_dir: str = "dist"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def package(self, name: str, team_config: Dict[str, Any], raw_config: Optional[Dict[str, Any]] = None) -> Path:
        deploy = (raw_config or {}).get("deploy", {}) or {}

        team_json = self._build_team_json(name, team_config, deploy)

        output_path = self.output_dir / f"{name}"
        output_path.mkdir(parents=True, exist_ok=True)

        json_path = output_path / "team.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(team_json, f, indent=2, ensure_ascii=False, default=str)

        return output_path

    def _build_team_json(self, name: str, team_config: Dict[str, Any], deploy: Dict[str, Any]) -> Dict[str, Any]:
        agents_refs = self._resolve_agent_refs(team_config)
        return {
            "schema_version": "1.0.0",
            "type": "team",
            "name": deploy.get("name", name),
            "version": deploy.get("version", "1.0.0"),
            "description": team_config.get("description", ""),
            "author": deploy.get("author", ""),
            "category": deploy.get("category", "general"),
            "tags": deploy.get("tags", []),
            "mode": team_config.get("mode", "collaborate"),
            "leader": team_config.get("leader") or {},
            "agents": agents_refs,
            "shared_state": team_config.get("shared_state", True),
            "instructions": team_config.get("instructions", []),
            "dependencies": self._build_dependencies(team_config),
        }

    def _resolve_agent_refs(self, team_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        agents = team_config.get("members", team_config.get("agents", [])) or []
        refs = []
        for agent in agents:
            if isinstance(agent, dict):
                agent_name = agent.get("name", "")
                refs.append({
                    "name": agent_name,
                    "ref_type": "inline" if agent.get("config") or len(agent) > 1 else "reference",
                })
            elif isinstance(agent, str):
                refs.append({"name": agent, "ref_type": "reference"})
        return refs

    def _build_dependencies(self, team_config: Dict[str, Any]) -> Dict[str, Any]:
        members = team_config.get("members", team_config.get("agents", [])) or []
        agent_count = 0
        for m in members:
            if isinstance(m, dict) and m.get("name"):
                agent_count += 1
            elif isinstance(m, str):
                agent_count += 1
        return {
            "agents": agent_count,
            "teams": 0,
        }

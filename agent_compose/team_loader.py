from typing import Any, Dict, List, Optional


class TeamLoader:
    """将 YAML Team 配置构建为 Team 配置对象。"""

    def __init__(self, agent_loader=None, config_resolver=None):
        self.agent_loader = agent_loader
        self.config_resolver = config_resolver
        self._cache: Dict[str, Dict[str, Any]] = {}

    def load(self, name: str, team_config: Dict[str, Any], base_dir: str = "") -> Dict[str, Any]:
        cache_key = f"{name}::{base_dir}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        resolved = self.config_resolver.resolve(team_config, base_dir) if self.config_resolver else team_config

        members = self._build_members(resolved, base_dir)

        result = {
            "name": name,
            "mode": self._build_mode(resolved),
            "description": resolved.get("description", ""),
            "instructions": self._ensure_list(resolved.get("instructions", [])),
            "leader": self._build_leader(resolved, base_dir),
            "members": members,
            "shared_state": resolved.get("shared_state", True),
        }

        self._cache[cache_key] = result
        return result

    def _build_mode(self, resolved: Dict[str, Any]) -> str:
        mode = resolved.get("mode", "collaborate")
        valid_modes = {"route", "collaborate", "coordinate"}
        if mode not in valid_modes:
            return "collaborate"
        return mode

    def _build_leader(self, resolved: Dict[str, Any], base_dir: str) -> Optional[Dict[str, Any]]:
        leader_config = resolved.get("leader")
        if not leader_config or not isinstance(leader_config, dict):
            return None

        if self.agent_loader is not None:
            leader_name = leader_config.get("name", f"{resolved.get('name', 'team')}_leader")
            return self.agent_loader.load(leader_name, leader_config, base_dir)
        return leader_config

    def _build_members(self, resolved: Dict[str, Any], base_dir: str) -> List[Dict[str, Any]]:
        agents_refs = resolved.get("agents", [])
        if not isinstance(agents_refs, list):
            return []

        members = []
        for ref in agents_refs:
            if isinstance(ref, dict):
                agent_name = ref.get("name", ref.get("ref", ""))
                agent_config = {k: v for k, v in ref.items() if k not in {"name", "ref"}}
                if self.agent_loader is not None and agent_config:
                    members.append(self.agent_loader.load(agent_name, agent_config, base_dir))
                else:
                    members.append({"name": agent_name, "config": agent_config})
            elif isinstance(ref, str):
                members.append({"name": ref, "ref_type": "reference"})
        return members

    def _ensure_list(self, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v) for v in value]
        return [str(value)]

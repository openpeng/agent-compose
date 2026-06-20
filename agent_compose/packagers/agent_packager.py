import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional


class AgentPackager:
    """将 Agent 配置打包为标准 agent.json 格式。"""

    def __init__(self, output_dir: str = "dist"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def package(self, name: str, agent_config: Dict[str, Any], raw_config: Optional[Dict[str, Any]] = None) -> Path:
        deploy = (raw_config or {}).get("deploy", {}) or {}

        agent_json = self._build_agent_json(name, agent_config, deploy)

        output_path = self.output_dir / f"{name}"
        output_path.mkdir(parents=True, exist_ok=True)

        json_path = output_path / "agent.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(agent_json, f, indent=2, ensure_ascii=False, default=str)

        instructions_text = self._build_instructions_text(agent_config)
        if instructions_text:
            (output_path / "instructions.md").write_text(instructions_text, encoding="utf-8")

        return output_path

    def _build_agent_json(self, name: str, agent_config: Dict[str, Any], deploy: Dict[str, Any]) -> Dict[str, Any]:
        capabilities = self._build_capabilities(agent_config)
        return {
            "schema_version": "1.0.0",
            "type": "agent",
            "name": deploy.get("name", name),
            "version": deploy.get("version", "1.0.0"),
            "description": agent_config.get("description", ""),
            "author": deploy.get("author", ""),
            "category": deploy.get("category", "general"),
            "tags": deploy.get("tags", []),
            "role": agent_config.get("role", ""),
            "instructions": agent_config.get("instructions", []),
            "tools": agent_config.get("tools", []),
            "model": agent_config.get("model", {}),
            "memory": agent_config.get("memory"),
            "knowledge": agent_config.get("knowledge"),
            "capabilities": capabilities,
            "compatibility": {
                "platforms": deploy.get("targets", ["cursor", "claude", "trae"]),
                "requires_agno": True,
            },
        }

    def _build_capabilities(self, agent_config: Dict[str, Any]) -> List[str]:
        caps: List[str] = []
        tools = agent_config.get("tools", []) or []
        for tool in tools:
            if isinstance(tool, dict):
                t = tool.get("type", "")
                if t == "mcp" and tool.get("name"):
                    caps.append(f"mcp:{tool['name']}")
                elif t == "skill" and tool.get("name"):
                    caps.append(f"skill:{tool['name']}")
                elif t == "builtin" and tool.get("name"):
                    caps.append(f"tool:{tool['name']}")
        return caps

    def _build_instructions_text(self, agent_config: Dict[str, Any]) -> str:
        instructions = agent_config.get("instructions", []) or []
        if isinstance(instructions, list):
            return "\n\n".join(str(i) for i in instructions)
        return str(instructions)

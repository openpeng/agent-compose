import os
import re
from pathlib import Path
from typing import Any, Dict, Optional


class DefinitionLoader:
    """加载 definitions/ 目录下的可复用定义。

    支持的定义类型：
    - skills.yml      - Skill 定义
    - mcps.yml        - MCP Server 定义
    - agents.yml      - Agent 模板定义
    - tools.yml       - 工具模板定义（可选）
    """

    def __init__(self, definitions_dir: str):
        self.definitions_dir = Path(definitions_dir)
        self._skills: Dict[str, Dict] = {}
        self._mcps: Dict[str, Dict] = {}
        self._agent_templates: Dict[str, Dict] = {}
        self._tool_templates: Dict[str, Dict] = {}
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        if not self.definitions_dir.exists():
            self._loaded = True
            return
        self._load_skills()
        self._load_mcps()
        self._load_agent_templates()
        self._load_tool_templates()
        self._loaded = True

    def _load_skills(self) -> None:
        path = self.definitions_dir / "skills.yml"
        if path.exists():
            data = _load_yaml(path) or {}
            self._skills = _extract_section(data, "skills")

    def _load_mcps(self) -> None:
        path = self.definitions_dir / "mcps.yml"
        if path.exists():
            data = _load_yaml(path) or {}
            self._mcps = _extract_section(data, "mcps")

    def _load_agent_templates(self) -> None:
        path = self.definitions_dir / "agents.yml"
        if path.exists():
            data = _load_yaml(path) or {}
            self._agent_templates = _extract_section(data, "agents")

    def _load_tool_templates(self) -> None:
        path = self.definitions_dir / "tools.yml"
        if path.exists():
            data = _load_yaml(path) or {}
            self._tool_templates = _extract_section(data, "tools")

    def get_skill(self, name: str) -> Optional[Dict[str, Any]]:
        self.load()
        return self._skills.get(name)

    def get_mcp(self, name: str) -> Optional[Dict[str, Any]]:
        self.load()
        return self._mcps.get(name)

    def get_agent_template(self, name: str) -> Optional[Dict[str, Any]]:
        self.load()
        return self._agent_templates.get(name)

    def get_tool_template(self, name: str) -> Optional[Dict[str, Any]]:
        self.load()
        return self._tool_templates.get(name)

    def list_skills(self) -> list:
        self.load()
        return list(self._skills.keys())

    def list_mcps(self) -> list:
        self.load()
        return list(self._mcps.keys())

    def list_agent_templates(self) -> list:
        self.load()
        return list(self._agent_templates.keys())


def _load_yaml(path: Path) -> Optional[Dict]:
    try:
        import yaml
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def _extract_section(data: Dict, section: str) -> Dict:
    if isinstance(data, dict) and section in data:
        item = data[section]
        if isinstance(item, dict):
            return item
    return {}

"""
VS Code Agent Mode 适配器

导入 VS Code 的 .vscode/agents/*.md 格式
"""

import os
import re
from typing import Any, Dict

from .base import ImportAdapter, slugify, extract_description, parse_frontmatter


class VSCodeAdapter(ImportAdapter):
    """VS Code Agent Mode Import Adapter

    VS Code Agent Mode 使用 .vscode/agents/ 目录存放 agent 定义文件。
    每个 agent 是一个 markdown 文件，包含:
    - YAML frontmatter (name, description, version, tools)
    - Markdown instructions

    参考: https://code.visualstudio.com/docs/copilot/chat/chat-agent-mode
    """

    def can_import(self, source_path: str) -> bool:
        normalized = source_path.replace("\\", "/")
        return ".vscode/agents" in normalized and normalized.endswith(".md")

    def import_from(self, source_path: str) -> Dict[str, Any]:
        if not os.path.exists(source_path):
            raise FileNotFoundError(f"VS Code agent file not found: {source_path}")

        with open(source_path, "r", encoding="utf-8") as f:
            content = f.read()

        file_name = os.path.splitext(os.path.basename(source_path))[0]
        name = slugify(file_name)

        frontmatter, body = parse_frontmatter(content)

        display_name = (
            frontmatter.get("name")
            or frontmatter.get("display_name")
            or self._extract_title(content)
            or file_name
        )

        description = (
            frontmatter.get("description")
            or extract_description(body)
            or f"Imported from VS Code Agent Mode: {file_name}"
        )

        # VS Code Agent Mode 支持 tools 声明
        capabilities = []
        tools = frontmatter.get("tools", [])
        if isinstance(tools, list):
            capabilities = [f"tool:{t}" for t in tools]

        return self._build_agent_json(
            name=name,
            display_name=display_name,
            description=description,
            content=body,
            version=frontmatter.get("version", "1.0.0"),
            author=frontmatter.get("author", ""),
            tags=["vscode", "agent_mode", "imported"],
            capabilities=capabilities,
            source="vscode",
            original_path=source_path,
        )

    def get_info(self) -> Dict[str, str]:
        return {
            "name": "vscode_agent_mode",
            "pattern": ".vscode/agents/*.md",
            "description": "Import agents from VS Code Agent Mode format",
        }

    def _extract_title(self, content: str) -> str:
        match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        return match.group(1).strip() if match else ""

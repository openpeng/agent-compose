"""
Claude Code 适配器

导入 Claude Code 的 .claude/commands/*.md 格式
"""

import os
import re
from typing import Any, Dict

from .base import ImportAdapter, slugify, extract_description, parse_frontmatter


class ClaudeAdapter(ImportAdapter):
    """Claude Code Import Adapter"""

    def can_import(self, source_path: str) -> bool:
        normalized = source_path.replace("\\", "/")
        return ".claude/commands" in normalized and normalized.endswith(".md")

    def import_from(self, source_path: str) -> Dict[str, Any]:
        if not os.path.exists(source_path):
            raise FileNotFoundError(f"Claude Code command file not found: {source_path}")

        with open(source_path, "r", encoding="utf-8") as f:
            content = f.read()

        file_name = os.path.splitext(os.path.basename(source_path))[0]
        name = slugify(file_name)

        frontmatter, body = parse_frontmatter(content)

        display_name = (
            frontmatter.get("name")
            or frontmatter.get("display_name")
            or self._extract_slash_command_name(content)
            or file_name
        )

        description = (
            frontmatter.get("description")
            or self._extract_description_from_content(body)
            or extract_description(body)
            or f"Imported from Claude Code: {file_name}"
        )

        return self._build_agent_json(
            name=name,
            display_name=display_name,
            description=description,
            content=body,
            version=frontmatter.get("version", "1.0.0"),
            author=frontmatter.get("author", ""),
            tags=["claude_code", "imported"],
            source="claude_code",
            original_path=source_path,
        )

    def get_info(self) -> Dict[str, str]:
        return {
            "name": "claude_code",
            "pattern": ".claude/commands/*.md",
            "description": "Import agents from Claude Code command format",
        }

    def _extract_slash_command_name(self, content: str) -> str:
        """提取 Claude Code slash command 名称

        格式: # /command-name -- Display Name
        """
        match = re.search(r"^#\s*/[\w-]+\s*[—–-]\s*(.+)$", content, re.MULTILINE)
        if match:
            return match.group(1).strip()

        # 回退到简单标题
        match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if match:
            return match.group(1).strip().lstrip("/")
        return ""

    def _extract_description_from_content(self, content: str) -> str:
        """从 ## Description 部分提取描述"""
        match = re.search(r"##\s+Description\s*\n\n(.+?)(?:\n\n|$)", content, re.DOTALL)
        if match:
            return match.group(1).strip().replace("\n", " ")
        return ""

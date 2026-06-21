"""
Cursor 适配器

导入 Cursor 的 .cursor/commands/*.md 格式
"""

import os
from typing import Any, Dict

from .base import ImportAdapter, slugify, extract_description, parse_frontmatter


class CursorAdapter(ImportAdapter):
    """Cursor Import Adapter"""

    def can_import(self, source_path: str) -> bool:
        normalized = source_path.replace("\\", "/")
        return ".cursor/commands" in normalized and normalized.endswith(".md")

    def import_from(self, source_path: str) -> Dict[str, Any]:
        if not os.path.exists(source_path):
            raise FileNotFoundError(f"Cursor command file not found: {source_path}")

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
            or f"Imported from Cursor: {file_name}"
        )

        return self._build_agent_json(
            name=name,
            display_name=display_name,
            description=description,
            content=body,
            version=frontmatter.get("version", "1.0.0"),
            author=frontmatter.get("author", ""),
            tags=["cursor", "imported"],
            source="cursor",
            original_path=source_path,
        )

    def get_info(self) -> Dict[str, str]:
        return {
            "name": "cursor",
            "pattern": ".cursor/commands/*.md",
            "description": "Import agents from Cursor command format",
        }

    def _extract_title(self, content: str) -> str:
        """从 markdown 提取标题"""
        import re

        match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        if match:
            return match.group(1).strip()
        return ""

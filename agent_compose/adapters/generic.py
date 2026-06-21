"""
Generic Markdown 适配器

通用 markdown 导入，作为最后的回退选项
"""

import os
import re
from typing import Any, Dict

from .base import ImportAdapter, slugify, extract_description, parse_frontmatter


class GenericMarkdownAdapter(ImportAdapter):
    """Generic Markdown Import Adapter

    通用 markdown 文件导入，作为最后的回退选项。
    支持任何包含 frontmatter 的 markdown 文件。
    """

    def can_import(self, source_path: str) -> bool:
        return source_path.endswith(".md") and os.path.exists(source_path)

    def import_from(self, source_path: str) -> Dict[str, Any]:
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
            or f"Imported from markdown: {file_name}"
        )

        return self._build_agent_json(
            name=name,
            display_name=display_name,
            description=description,
            content=body,
            version=frontmatter.get("version", "1.0.0"),
            author=frontmatter.get("author", ""),
            tags=["markdown", "imported"],
            source="generic_markdown",
            original_path=source_path,
        )

    def get_info(self) -> Dict[str, str]:
        return {
            "name": "generic_markdown",
            "pattern": "*.md",
            "description": "Import agents from generic markdown files with frontmatter",
        }

    def _extract_title(self, content: str) -> str:
        match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        return match.group(1).strip() if match else ""

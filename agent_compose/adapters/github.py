"""
GitHub Copilot 适配器

导入 GitHub 的 .github/agents/*.md 格式
"""

import os
import re
from typing import Any, Dict

from .base import ImportAdapter, slugify, extract_description, parse_frontmatter


class GitHubAdapter(ImportAdapter):
    """GitHub Copilot Import Adapter"""

    def can_import(self, source_path: str) -> bool:
        normalized = source_path.replace("\\", "/")
        return ".github/agents" in normalized and normalized.endswith(".md")

    def import_from(self, source_path: str) -> Dict[str, Any]:
        if not os.path.exists(source_path):
            raise FileNotFoundError(f"GitHub agent file not found: {source_path}")

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
            or f"Imported from GitHub Copilot: {file_name}"
        )

        return self._build_agent_json(
            name=name,
            display_name=display_name,
            description=description,
            content=body,
            version=frontmatter.get("version", "1.0.0"),
            author=frontmatter.get("author", ""),
            tags=["github_copilot", "imported"],
            source="github_copilot",
            original_path=source_path,
        )

    def get_info(self) -> Dict[str, str]:
        return {
            "name": "github_copilot",
            "pattern": ".github/agents/*.md",
            "description": "Import agents from GitHub Copilot agent format",
        }

    def _extract_title(self, content: str) -> str:
        match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        return match.group(1).strip() if match else ""

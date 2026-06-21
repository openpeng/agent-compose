"""
CodeBuddy 适配器

导入 CodeBuddy 的 .codebuddy/skills/[name]/SKILL.md 格式
"""

import os
from typing import Any, Dict

from .base import ImportAdapter, slugify, extract_description, parse_frontmatter


class CodeBuddyAdapter(ImportAdapter):
    """CodeBuddy Import Adapter"""

    def can_import(self, source_path: str) -> bool:
        normalized = source_path.replace("\\", "/")
        return ".codebuddy/skills" in normalized and normalized.endswith("SKILL.md")

    def import_from(self, source_path: str) -> Dict[str, Any]:
        if not os.path.exists(source_path):
            raise FileNotFoundError(f"CodeBuddy SKILL.md file not found: {source_path}")

        with open(source_path, "r", encoding="utf-8") as f:
            content = f.read()

        skill_dir = os.path.basename(os.path.dirname(source_path))
        name = slugify(skill_dir)

        frontmatter, body = parse_frontmatter(content)

        if not frontmatter.get("name") and not frontmatter.get("display_name"):
            raise ValueError(
                f"CodeBuddy SKILL.md must have YAML frontmatter with 'name' field.\nFile: {source_path}"
            )

        display_name = frontmatter.get("display_name") or frontmatter.get("name") or skill_dir
        description = frontmatter.get("description") or extract_description(body) or ""

        tags = list(frontmatter.get("tags", [])) if isinstance(frontmatter.get("tags"), list) else []
        tags.extend(["codebuddy", "imported"])

        return self._build_agent_json(
            name=slugify(frontmatter.get("name")) if frontmatter.get("name") else name,
            display_name=display_name,
            description=description,
            content=body,
            version=frontmatter.get("version", "1.0.0"),
            author=frontmatter.get("author", ""),
            tags=tags,
            capabilities=frontmatter.get("capabilities", []),
            source="codebuddy",
            original_path=source_path,
        )

    def get_info(self) -> Dict[str, str]:
        return {
            "name": "codebuddy",
            "pattern": ".codebuddy/skills/*/SKILL.md",
            "description": "Import agents from CodeBuddy skill format",
        }

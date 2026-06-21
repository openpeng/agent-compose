"""
JetBrains AI Assistant 适配器

导入 JetBrains 的 .idea/ai-assistant/*.md 格式
"""

import os
import re
from typing import Any, Dict

from .base import ImportAdapter, slugify, extract_description, parse_frontmatter


class JetBrainsAdapter(ImportAdapter):
    """JetBrains AI Assistant Import Adapter

    JetBrains AI Assistant 使用 .idea/ai-assistant/ 目录存放 prompt 文件。
    每个 prompt 是一个 markdown 文件，包含:
    - YAML frontmatter (name, description, category)
    - Markdown prompt template

    支持变量占位符: {{variable_name}}
    """

    def can_import(self, source_path: str) -> bool:
        normalized = source_path.replace("\\", "/")
        return ".idea/ai-assistant" in normalized and normalized.endswith(".md")

    def import_from(self, source_path: str) -> Dict[str, Any]:
        if not os.path.exists(source_path):
            raise FileNotFoundError(f"JetBrains AI Assistant file not found: {source_path}")

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
            or f"Imported from JetBrains AI Assistant: {file_name}"
        )

        # 提取变量占位符
        variables = self._extract_variables(body)

        agent_json = self._build_agent_json(
            name=name,
            display_name=display_name,
            description=description,
            content=body,
            version=frontmatter.get("version", "1.0.0"),
            author=frontmatter.get("author", ""),
            tags=["jetbrains", "ai_assistant", "imported"],
            source="jetbrains",
            original_path=source_path,
        )

        # 添加变量信息
        if variables:
            agent_json["variables"] = variables

        return agent_json

    def get_info(self) -> Dict[str, str]:
        return {
            "name": "jetbrains_ai_assistant",
            "pattern": ".idea/ai-assistant/*.md",
            "description": "Import agents from JetBrains AI Assistant format",
        }

    def _extract_title(self, content: str) -> str:
        match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
        return match.group(1).strip() if match else ""

    def _extract_variables(self, content: str) -> Dict[str, str]:
        """提取 {{variable}} 占位符"""
        variables = {}
        matches = re.findall(r"\{\{(\w+)\}\}", content)
        for var in set(matches):
            variables[var] = f"Variable: {var}"
        return variables

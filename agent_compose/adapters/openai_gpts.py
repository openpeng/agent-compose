"""
OpenAI GPTs 适配器

导入 OpenAI GPTs 的 gpts/*.json 格式
"""

import json
import os
from typing import Any, Dict

from .base import ImportAdapter, slugify


class OpenAIGPTsAdapter(ImportAdapter):
    """OpenAI GPTs Import Adapter

    OpenAI GPTs 使用 JSON 格式定义:
    {
        "name": "GPT Name",
        "description": "Description",
        "instructions": "System instructions",
        "conversation_starters": ["..."],
        "capabilities": ["web_browsing", "code_interpreter", "dalle"]
    }
    """

    def can_import(self, source_path: str) -> bool:
        normalized = source_path.replace("\\", "/")
        return ("gpts/" in normalized or "gpts\\" in source_path) and normalized.endswith(".json")

    def import_from(self, source_path: str) -> Dict[str, Any]:
        if not os.path.exists(source_path):
            raise FileNotFoundError(f"OpenAI GPTs file not found: {source_path}")

        with open(source_path, "r", encoding="utf-8") as f:
            gpt_data = json.load(f)

        name = slugify(gpt_data.get("name", os.path.splitext(os.path.basename(source_path))[0]))
        display_name = gpt_data.get("name", name)
        description = gpt_data.get("description", f"Imported from OpenAI GPTs: {display_name}")
        instructions = gpt_data.get("instructions", "")

        # 映射 capabilities
        capabilities = []
        caps = gpt_data.get("capabilities", [])
        if isinstance(caps, list):
            for cap in caps:
                if cap == "web_browsing":
                    capabilities.append("web_search")
                elif cap == "code_interpreter":
                    capabilities.append("code_execution")
                elif cap == "dalle":
                    capabilities.append("image_generation")
                else:
                    capabilities.append(cap)

        # 构建 conversation starters 作为 instructions 的补充
        starters = gpt_data.get("conversation_starters", [])
        if starters:
            starters_md = "\n\n## Conversation Starters\n\n" + "\n".join(f"- {s}" for s in starters)
            instructions += starters_md

        return self._build_agent_json(
            name=name,
            display_name=display_name,
            description=description,
            content=instructions,
            version="1.0.0",
            author="Imported from OpenAI GPTs",
            tags=["openai", "gpts", "imported"],
            capabilities=capabilities,
            source="openai_gpts",
            original_path=source_path,
        )

    def get_info(self) -> Dict[str, str]:
        return {
            "name": "openai_gpts",
            "pattern": "gpts/*.json",
            "description": "Import agents from OpenAI GPTs format",
        }

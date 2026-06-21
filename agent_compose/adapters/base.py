"""
适配器基类和注册表
"""

import os
import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


def slugify(text: str) -> str:
    """将文本转换为 URL-friendly slug"""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text.strip("-")


def extract_description(content: str, max_length: int = 200) -> str:
    """从 markdown 内容提取描述（第一段非空文本）"""
    # 移除 frontmatter
    content = re.sub(r"^---\n.*?\n---\n", "", content, flags=re.DOTALL)
    # 移除标题
    content = re.sub(r"^#+\s+.*$", "", content, flags=re.MULTILINE)
    # 提取第一段
    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    if paragraphs:
        desc = paragraphs[0].replace("\n", " ")
        if len(desc) > max_length:
            desc = desc[:max_length].rsplit(" ", 1)[0] + "..."
        return desc
    return ""


def parse_frontmatter(content: str) -> tuple:
    """解析 YAML frontmatter

    Returns:
        (frontmatter_dict, body)
    """
    if not content.startswith("---"):
        return {}, content

    match = re.match(r"^---\n(.*?)\n---\n?(.*)$", content, re.DOTALL)
    if not match:
        return {}, content

    frontmatter_text = match.group(1)
    body = match.group(2)

    # 简单 YAML 解析
    frontmatter: Dict[str, Any] = {}
    for line in frontmatter_text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            # 尝试解析为列表
            if value.startswith("[") and value.endswith("]"):
                value = [v.strip().strip('"').strip("'") for v in value[1:-1].split(",")]
            # 尝试解析为布尔值
            elif value.lower() == "true":
                value = True
            elif value.lower() == "false":
                value = False
            # 尝试解析为数字
            else:
                try:
                    if "." in value:
                        value = float(value)
                    else:
                        value = int(value)
                except ValueError:
                    pass
            frontmatter[key] = value

    return frontmatter, body


class ImportAdapter(ABC):
    """导入适配器基类"""

    @abstractmethod
    def can_import(self, source_path: str) -> bool:
        """检查是否可以处理给定的源路径"""
        pass

    @abstractmethod
    def import_from(self, source_path: str) -> Dict[str, Any]:
        """从源路径导入为 Agent JSON v2 格式"""
        pass

    @abstractmethod
    def get_info(self) -> Dict[str, str]:
        """获取适配器信息"""
        pass

    def _build_agent_json(
        self,
        name: str,
        display_name: str,
        description: str,
        content: str,
        version: str = "1.0.0",
        author: str = "",
        tags: Optional[List[str]] = None,
        capabilities: Optional[List[str]] = None,
        source: str = "",
        original_path: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """构建标准 Agent JSON v2 结构"""
        agent_json: Dict[str, Any] = {
            "schema_version": "2.0",
            "identity": {
                "name": name,
                "version": version,
                "display_name": display_name,
                "description": description,
                "author": author or f"Imported from {source}",
                "tags": tags or [source, "imported"],
            },
            "instructions": {
                "format": "markdown",
                "source": "inline",
                "content": content,
            },
            "capabilities": capabilities or [],
            "compatibility": {
                source: True,
                "source": source,
                "original_path": original_path,
            },
        }
        if extra:
            agent_json.update(extra)
        return agent_json


class AdapterRegistry:
    """适配器注册表"""

    def __init__(self):
        self._adapters: List[ImportAdapter] = []

    def register(self, adapter: ImportAdapter) -> None:
        """注册适配器"""
        self._adapters.append(adapter)

    def find_adapter(self, source_path: str) -> Optional[ImportAdapter]:
        """查找可以处理给定路径的适配器"""
        for adapter in self._adapters:
            if adapter.can_import(source_path):
                return adapter
        return None

    def import_agent(self, source_path: str) -> Dict[str, Any]:
        """导入 Agent，自动选择适配器"""
        adapter = self.find_adapter(source_path)
        if not adapter:
            raise ValueError(f"No adapter found for: {source_path}")
        return adapter.import_from(source_path)

    def list_adapters(self) -> List[Dict[str, str]]:
        """列出所有适配器信息"""
        return [adapter.get_info() for adapter in self._adapters]

    def scan_directory(self, directory: str) -> List[str]:
        """扫描目录，返回所有可导入的文件路径"""
        results = []
        for root, _, files in os.walk(directory):
            for file in files:
                path = os.path.join(root, file)
                if self.find_adapter(path):
                    results.append(path)
        return results

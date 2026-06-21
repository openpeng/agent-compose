"""
Tools - 内置工具实现

从 agent-deploy 迁移并增强的内置工具集合。
包含: bash, read_file, write_file, glob, llm_chat, web_search, web_fetch
"""

from .bash import bash_tool
from .read_file import read_file_tool
from .write_file import write_file_tool
from .glob_tool import glob_tool
from .llm_chat import llm_chat_tool
from .web_search import web_search_tool
from .web_fetch import web_fetch_tool

__all__ = [
    "bash_tool",
    "read_file_tool",
    "write_file_tool",
    "glob_tool",
    "llm_chat_tool",
    "web_search_tool",
    "web_fetch_tool",
]


def register_builtin_tools(registry) -> None:
    """将所有内置工具注册到 ToolRegistry"""
    from ..pipeline_engine import ToolRegistry

    registry.register("bash", bash_tool)
    registry.register("read_file", read_file_tool)
    registry.register("write_file", write_file_tool)
    registry.register("glob", glob_tool)
    registry.register("llm_chat", llm_chat_tool)
    registry.register("web_search", web_search_tool)
    registry.register("web_fetch", web_fetch_tool)

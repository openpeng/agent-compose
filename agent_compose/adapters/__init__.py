"""
Adapters - 跨平台适配器

支持从多种 AI 工具格式导入 Agent 配置:
- Cursor (.cursor/commands/*.md)
- Claude Code (.claude/commands/*.md)
- CodeBuddy (.codebuddy/skills/*/SKILL.md)
- GitHub Copilot (.github/agents/*.md)
- VS Code Agent Mode (.vscode/agents/*.md)
- JetBrains AI Assistant (.idea/ai-assistant/*.md)
- OpenAI GPTs (gpts/*.json)
- Generic Markdown (*.md with frontmatter)
"""

from .base import ImportAdapter, AdapterRegistry
from .cursor import CursorAdapter
from .claude import ClaudeAdapter
from .codebuddy import CodeBuddyAdapter
from .github import GitHubAdapter
from .vscode import VSCodeAdapter
from .jetbrains import JetBrainsAdapter
from .openai_gpts import OpenAIGPTsAdapter
from .generic import GenericMarkdownAdapter

__all__ = [
    "ImportAdapter",
    "AdapterRegistry",
    "CursorAdapter",
    "ClaudeAdapter",
    "CodeBuddyAdapter",
    "GitHubAdapter",
    "VSCodeAdapter",
    "JetBrainsAdapter",
    "OpenAIGPTsAdapter",
    "GenericMarkdownAdapter",
]


def create_default_registry() -> AdapterRegistry:
    """创建包含所有适配器的默认注册表"""
    registry = AdapterRegistry()
    registry.register(CursorAdapter())
    registry.register(ClaudeAdapter())
    registry.register(CodeBuddyAdapter())
    registry.register(GitHubAdapter())
    registry.register(VSCodeAdapter())
    registry.register(JetBrainsAdapter())
    registry.register(OpenAIGPTsAdapter())
    registry.register(GenericMarkdownAdapter())
    return registry

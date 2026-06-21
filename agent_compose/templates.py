"""
agent-compose 模板生成模块

提供 Agent、Worker、Team 的模板生成函数，以及预置的常用模板。
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml


# ============================================================
# 核心模板生成函数
# ============================================================

def generate_agent_template(
    name: str,
    description: str,
    tools: Optional[List[str]] = None,
    model_provider: str = "openrouter",
    model_id: str = "openrouter/free",
) -> Dict[str, Any]:
    """
    生成 agent.json 字典

    Args:
        name: Agent 名称（英文标识）
        description: Agent 描述
        tools: 工具列表，如 ["read_file", "write_file", "bash"]
        model_provider: 模型提供商
        model_id: 模型 ID

    Returns:
        agent.json 对应的字典
    """
    tools = tools or []
    display_name = name.replace("_", " ").title()

    return {
        "schema_version": "2.0",
        "identity": {
            "name": name,
            "version": "0.1.0",
            "display_name": display_name,
            "description": description,
            "author": os.environ.get("USER") or os.environ.get("USERNAME") or "Unknown",
            "license": "MIT",
            "tags": ["agent-compose"],
        },
        "instructions": {
            "format": "markdown",
            "source": "inline",
            "content": f"You are {display_name}. {description}",
        },
        "capabilities": [
            {"type": "tool", "name": tool, "description": f"Use {tool} tool"}
            for tool in tools
        ],
        "model": {
            "provider": model_provider,
            "model_id": model_id,
        },
        "mcp_servers": [],
    }


def generate_worker_template(
    agent_name: str,
    steps: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    生成 worker.yaml 字典

    Args:
        agent_name: 关联的 Agent 名称
        steps: 工作步骤列表

    Returns:
        worker.yaml 对应的字典
    """
    steps = steps or [
        {
            "name": "step_1",
            "agent": agent_name,
            "prompt": "Execute the main task",
        }
    ]

    return {
        "version": "1.0",
        "worker": {
            "name": f"{agent_name}_worker",
            "description": f"Worker for {agent_name}",
            "agent": agent_name,
            "steps": steps,
        },
    }


def generate_team_template(
    name: str,
    members: Optional[List[Dict[str, Any]]] = None,
    mode: str = "sequential",
) -> Dict[str, Any]:
    """
    生成 team.yaml 字典

    Args:
        name: Team 名称
        members: 成员列表，每个成员包含 name、role、agent
        mode: 协作模式，如 sequential、parallel、supervisor

    Returns:
        team.yaml 对应的字典
    """
    members = members or []
    display_name = name.replace("_", " ").title()

    return {
        "version": "1.0",
        "team": {
            "name": name,
            "display_name": display_name,
            "description": f"Team: {display_name}",
            "mode": mode,
            "members": members,
        },
    }


# ============================================================
# 文件写入
# ============================================================

def write_template_to_file(template_dict: Dict[str, Any], output_path: str) -> str:
    """
    将模板字典写入文件，根据扩展名自动选择 JSON 或 YAML 格式

    Args:
        template_dict: 模板字典
        output_path: 输出文件路径

    Returns:
        写入的文件路径
    """
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    suffix = path.suffix.lower()

    if suffix == ".json":
        path.write_text(
            json.dumps(template_dict, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    elif suffix in (".yaml", ".yml"):
        path.write_text(
            yaml.dump(template_dict, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
    else:
        # 默认 JSON
        path.write_text(
            json.dumps(template_dict, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    return str(path.absolute())


# ============================================================
# 预置模板
# ============================================================

code_reviewer = {
    "schema_version": "2.0",
    "identity": {
        "name": "code_reviewer",
        "version": "1.0.0",
        "display_name": "Code Reviewer",
        "description": "Reviews code for quality, bugs, security issues, and best practices.",
        "author": "agent-compose",
        "license": "MIT",
        "tags": ["code-review", "quality", "security"],
    },
    "instructions": {
        "format": "markdown",
        "source": "inline",
        "content": (
            "You are an expert code reviewer. Analyze code for:\n"
            "- Bugs and logical errors\n"
            "- Security vulnerabilities\n"
            "- Performance issues\n"
            "- Code style and best practices\n"
            "- Missing tests or documentation\n\n"
            "Provide actionable feedback with specific line references."
        ),
    },
    "capabilities": [
        {"type": "tool", "name": "read_file", "description": "Read source code files"},
        {"type": "tool", "name": "bash", "description": "Run linters and tests"},
        {"type": "tool", "name": "web_search", "description": "Search for best practices"},
    ],
    "model": {
        "provider": "openrouter",
        "model_id": "openrouter/free",
    },
    "mcp_servers": [],
}


test_generator = {
    "schema_version": "2.0",
    "identity": {
        "name": "test_generator",
        "version": "1.0.0",
        "display_name": "Test Generator",
        "description": "Generates comprehensive unit and integration tests for code.",
        "author": "agent-compose",
        "license": "MIT",
        "tags": ["testing", "automation", "quality"],
    },
    "instructions": {
        "format": "markdown",
        "source": "inline",
        "content": (
            "You are a test automation expert. Generate comprehensive tests including:\n"
            "- Unit tests for individual functions\n"
            "- Integration tests for modules\n"
            "- Edge case and boundary testing\n"
            "- Mock and fixture setup\n\n"
            "Follow testing best practices for the target language and framework."
        ),
    },
    "capabilities": [
        {"type": "tool", "name": "read_file", "description": "Read source code"},
        {"type": "tool", "name": "write_file", "description": "Write test files"},
        {"type": "tool", "name": "bash", "description": "Run test commands"},
    ],
    "model": {
        "provider": "openrouter",
        "model_id": "openrouter/free",
    },
    "mcp_servers": [],
}


doc_translator = {
    "schema_version": "2.0",
    "identity": {
        "name": "doc_translator",
        "version": "1.0.0",
        "display_name": "Documentation Translator",
        "description": "Translates documentation between languages while preserving technical accuracy.",
        "author": "agent-compose",
        "license": "MIT",
        "tags": ["documentation", "translation", "i18n"],
    },
    "instructions": {
        "format": "markdown",
        "source": "inline",
        "content": (
            "You are a technical documentation translator.\n"
            "- Preserve code blocks and technical terms\n"
            "- Maintain Markdown formatting\n"
            "- Use consistent terminology\n"
            "- Keep front-matter metadata intact\n"
            "- Ensure translated content is natural and accurate"
        ),
    },
    "capabilities": [
        {"type": "tool", "name": "read_file", "description": "Read documentation files"},
        {"type": "tool", "name": "write_file", "description": "Write translated files"},
        {"type": "tool", "name": "glob", "description": "Find documentation files"},
    ],
    "model": {
        "provider": "openrouter",
        "model_id": "openrouter/free",
    },
    "mcp_servers": [],
}


bug_fixer = {
    "schema_version": "2.0",
    "identity": {
        "name": "bug_fixer",
        "version": "1.0.0",
        "display_name": "Bug Fixer",
        "description": "Analyzes bug reports and code to identify and fix issues.",
        "author": "agent-compose",
        "license": "MIT",
        "tags": ["debugging", "bug-fix", "automation"],
    },
    "instructions": {
        "format": "markdown",
        "source": "inline",
        "content": (
            "You are a debugging expert. Your workflow:\n"
            "1. Analyze error messages and stack traces\n"
            "2. Read relevant source code\n"
            "3. Identify root cause\n"
            "4. Implement minimal fix\n"
            "5. Verify fix with tests\n\n"
            "Always explain the root cause and your fix."
        ),
    },
    "capabilities": [
        {"type": "tool", "name": "read_file", "description": "Read source code"},
        {"type": "tool", "name": "write_file", "description": "Apply fixes"},
        {"type": "tool", "name": "bash", "description": "Run reproduction and tests"},
        {"type": "tool", "name": "web_search", "description": "Search for similar issues"},
    ],
    "model": {
        "provider": "openrouter",
        "model_id": "openrouter/free",
    },
    "mcp_servers": [],
}


# 预置模板注册表
BUILTIN_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "code_reviewer": code_reviewer,
    "test_generator": test_generator,
    "doc_translator": doc_translator,
    "bug_fixer": bug_fixer,
}


def list_builtin_templates() -> List[Dict[str, str]]:
    """
    列出所有可用的预置模板

    Returns:
        模板信息列表
    """
    return [
        {
            "id": tid,
            "name": tpl["identity"]["display_name"],
            "description": tpl["identity"]["description"],
            "version": tpl["identity"]["version"],
            "tags": ", ".join(tpl["identity"].get("tags", [])),
        }
        for tid, tpl in BUILTIN_TEMPLATES.items()
    ]


def get_builtin_template(template_id: str) -> Optional[Dict[str, Any]]:
    """
    获取指定预置模板

    Args:
        template_id: 模板 ID

    Returns:
        模板字典，如果不存在则返回 None
    """
    tpl = BUILTIN_TEMPLATES.get(template_id)
    if tpl is None:
        return None
    # 返回深拷贝，避免修改原始模板
    import copy
    return copy.deepcopy(tpl)

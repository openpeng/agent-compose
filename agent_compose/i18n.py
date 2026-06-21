"""
i18n - Internationalization support for agent-compose CLI.

Provides message catalog with Chinese and English messages,
auto-detection from LC_ALL/LANG environment variables,
and a simple get_message() API.
"""

import os
import re
from typing import Optional

# ============================================================
# Message Catalog
# ============================================================

MESSAGES: dict[str, dict[str, str]] = {
    "zh": {
        # Common status
        "success": "成功",
        "error": "错误",
        "warning": "警告",
        "info": "信息",
        "cancelled": "已取消",
        "done": "完成",
        "loading": "加载中",
        "processing": "处理中",
        # Prompts
        "prompt_confirm_default_yes": "{message} [Y/n]: ",
        "prompt_confirm_default_no": "{message} [y/N]: ",
        # CLI outputs
        "cli_agents_count": "Agents ({count}):",
        "cli_teams_count": "Teams ({count}):",
        "cli_workflows_count": "Workflows ({count}):",
        "cli_agent_not_found": "未找到 Agent '{name}'",
        "cli_team_not_found": "未找到 Team '{name}'",
        "cli_workflow_not_found": "未找到 Workflow '{name}'",
        "cli_loaded_entities": "已加载 {count} 个实体",
        "cli_packaged_to": "已将 '{name}' 打包到: {path}",
        "cli_deployed": "已部署 '{name}'",
        "cli_server_starting": "正在启动 AgentRuntime HTTP 服务器 ...",
        "cli_server_registering": "正在向 AgentOS 注册 ...",
        # Market
        "market_service": "市场服务",
        "market_status": "状态",
        "market_version": "版本",
        "market_agents_count": "Agents 数量",
        "market_teams_count": "Teams 数量",
        "market_workflows_count": "Workflows 数量",
        "market_searching": "正在搜索 '{query}' 在 {type} 中 (limit={limit})",
        "market_no_results": "(无结果)",
        "market_total_results": "共 {count} 条结果",
        "market_downloading": "正在下载 '{agent_id}' ...",
        "market_download_failed": "下载失败: {error}",
        "market_got_from_cache": "已从缓存获取 agent.json",
        "market_got_from_market": "已从市场获取 agent.json",
        "market_saved_to": "已保存到 {path}",
        "market_agent_details": "Agent 详情",
        "market_schema_version": "Schema 版本",
        "market_author": "作者",
        "market_category": "分类",
        "market_tags": "标签",
        "market_capabilities": "能力 ({count})",
        "market_mcp_servers": "MCP 服务器 ({count})",
        "market_system_prompt": "系统提示词 (前 400 字符)",
        # Run
        "run_preparing": "准备运行 '{agent_id}' ...",
        "run_agent": "Agent",
        "run_schema": "Schema",
        "run_source": "来源",
        "run_model": "模型",
        "run_base_url": "Base URL",
        "run_api_key": "API Key",
        "run_webbridge_token": "WebBridge Token",
        "run_init_mcp": "正在初始化 MCP 服务器 ...",
        "run_connected_mcps": "已连接 {count} 个 MCP 服务器: {names}",
        "run_mcp_required_prompt": "此 Agent 需要 MCP 服务器但未连接。是否继续?",
        "run_interactive_mode": "(交互模式 — 输入消息, 'quit' 退出)",
        "run_chat_complete": "对话完成, {count} 轮",
        # Errors
        "err_no_api_key": "未提供 API Key。请使用 --api-key 或设置 OPENROUTER_API_KEY / KIMI_API_KEY 环境变量。",
        "err_market_connect": "无法连接市场服务: {error}",
        "err_unknown_command": "未知命令: {cmd}",
        # Spinner / Progress
        "spinner_default": "处理中 ...",
        "spinner_download": "正在下载 ...",
        "spinner_execute": "正在执行 ...",
        "spinner_deploy": "正在部署 ...",
    },
    "en": {
        # Common status
        "success": "Success",
        "error": "Error",
        "warning": "Warning",
        "info": "Info",
        "cancelled": "Cancelled",
        "done": "Done",
        "loading": "Loading",
        "processing": "Processing",
        # Prompts
        "prompt_confirm_default_yes": "{message} [Y/n]: ",
        "prompt_confirm_default_no": "{message} [y/N]: ",
        # CLI outputs
        "cli_agents_count": "Agents ({count}):",
        "cli_teams_count": "Teams ({count}):",
        "cli_workflows_count": "Workflows ({count}):",
        "cli_agent_not_found": "Agent '{name}' not found",
        "cli_team_not_found": "Team '{name}' not found",
        "cli_workflow_not_found": "Workflow '{name}' not found",
        "cli_loaded_entities": "Loaded {count} entities",
        "cli_packaged_to": "Packaged '{name}' to: {path}",
        "cli_deployed": "Deployed '{name}'",
        "cli_server_starting": "Starting AgentRuntime HTTP server ...",
        "cli_server_registering": "Registering with AgentOS ...",
        # Market
        "market_service": "Market Service",
        "market_status": "Status",
        "market_version": "Version",
        "market_agents_count": "Agents Count",
        "market_teams_count": "Teams Count",
        "market_workflows_count": "Workflows Count",
        "market_searching": "Searching '{query}' in {type} (limit={limit})",
        "market_no_results": "(no results)",
        "market_total_results": "{count} total results",
        "market_downloading": "Downloading '{agent_id}' ...",
        "market_download_failed": "Download failed: {error}",
        "market_got_from_cache": "Got agent.json from cache",
        "market_got_from_market": "Got agent.json from market",
        "market_saved_to": "Saved to {path}",
        "market_agent_details": "Agent Details",
        "market_schema_version": "Schema Version",
        "market_author": "Author",
        "market_category": "Category",
        "market_tags": "Tags",
        "market_capabilities": "Capabilities ({count})",
        "market_mcp_servers": "MCP Servers ({count})",
        "market_system_prompt": "System Prompt (first 400 chars)",
        # Run
        "run_preparing": "Preparing to run '{agent_id}' ...",
        "run_agent": "Agent",
        "run_schema": "Schema",
        "run_source": "Source",
        "run_model": "Model",
        "run_base_url": "Base URL",
        "run_api_key": "API Key",
        "run_webbridge_token": "WebBridge Token",
        "run_init_mcp": "Initializing MCP servers ...",
        "run_connected_mcps": "Connected {count} MCP server(s): {names}",
        "run_mcp_required_prompt": "This agent requires MCP servers but none are connected. Continue?",
        "run_interactive_mode": "(interactive mode — type your message, 'quit' to exit)",
        "run_chat_complete": "Chat complete, {count} turn(s)",
        # Errors
        "err_no_api_key": "No API key provided. Use --api-key or set OPENROUTER_API_KEY / KIMI_API_KEY env variable.",
        "err_market_connect": "Cannot connect to market service: {error}",
        "err_unknown_command": "Unknown command: {cmd}",
        # Spinner / Progress
        "spinner_default": "Processing ...",
        "spinner_download": "Downloading ...",
        "spinner_execute": "Executing ...",
        "spinner_deploy": "Deploying ...",
    },
}


# ============================================================
# Language Detection
# ============================================================

def detect_language() -> str:
    """Auto-detect language from LC_ALL / LANG environment variables."""
    for env_var in ("LC_ALL", "LANG", "LANGUAGE"):
        value = os.environ.get(env_var, "")
        if value:
            # e.g. "zh_CN.UTF-8" -> "zh"
            lang = value.split(".")[0].split("_")[0].lower()
            if lang in MESSAGES:
                return lang
            # Also accept "zh" prefix for any Chinese locale
            if lang.startswith("zh"):
                return "zh"
    return "en"


# ============================================================
# Public API
# ============================================================

def get_message(key: str, lang: Optional[str] = None, **kwargs) -> str:
    """
    Retrieve a localized message by key.

    Args:
        key: Message catalog key.
        lang: Language code ('zh' or 'en'). Auto-detected if None.
        **kwargs: Format placeholders.

    Returns:
        Localized (and optionally formatted) string.
    """
    if lang is None:
        lang = detect_language()
    lang = lang.lower()
    if lang not in MESSAGES:
        lang = "en"

    template = MESSAGES[lang].get(key, MESSAGES["en"].get(key, key))
    if kwargs:
        try:
            return template.format(**kwargs)
        except KeyError:
            # If formatting fails, return raw template
            return template
    return template


# Convenience alias
_ = get_message

"""
agent-compose 交互式设置向导

提供交互式 CLI 向导，引导用户创建 Agent、Worker 或 Team 配置。
"""

import os
import sys
from typing import Any, Dict, List, Optional

from agent_compose.templates import (
    generate_agent_template,
    generate_worker_template,
    generate_team_template,
    write_template_to_file,
    list_builtin_templates,
    get_builtin_template,
)


# ============================================================
# 交互式辅助函数
# ============================================================

def _ask(question: str, default: str = "") -> str:
    """询问用户输入"""
    if default:
        prompt = f"{question} [{default}]: "
    else:
        prompt = f"{question}: "
    try:
        answer = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。")
        sys.exit(0)
    return answer if answer else default


def _ask_choice(question: str, options: List[str], default: int = 0) -> int:
    """让用户从列表中选择"""
    print(f"\n{question}")
    for i, opt in enumerate(options, 1):
        marker = " (默认)" if i - 1 == default else ""
        print(f"  [{i}] {opt}{marker}")
    while True:
        try:
            answer = input(f"请选择 [1-{len(options)}]: ").strip()
            if not answer:
                return default
            idx = int(answer) - 1
            if 0 <= idx < len(options):
                return idx
            print(f"请输入 1 到 {len(options)} 之间的数字。")
        except ValueError:
            print("请输入有效的数字。")
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            sys.exit(0)


def _ask_multiselect(question: str, options: List[str]) -> List[str]:
    """让用户多选"""
    print(f"\n{question}")
    print("  [0] 完成选择")
    for i, opt in enumerate(options, 1):
        print(f"  [{i}] {opt}")
    selected: List[str] = []
    while True:
        try:
            answer = input("选择编号（0 完成）: ").strip()
            if not answer or answer == "0":
                break
            indices = [int(x.strip()) - 1 for x in answer.split(",") if x.strip()]
            for idx in indices:
                if 0 <= idx < len(options):
                    if options[idx] not in selected:
                        selected.append(options[idx])
                        print(f"  + {options[idx]}")
                else:
                    print(f"  忽略无效选项: {idx + 1}")
        except ValueError:
            print("  请输入数字，用逗号分隔。")
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            sys.exit(0)
    return selected


def _ask_yes_no(question: str, default: bool = True) -> bool:
    """询问是/否"""
    suffix = " [Y/n]: " if default else " [y/N]: "
    try:
        answer = input(f"{question}{suffix}").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。")
        sys.exit(0)
    if not answer:
        return default
    return answer in ("y", "yes", "是")


# ============================================================
# 可用选项定义
# ============================================================

AVAILABLE_TOOLS = [
    "read_file",
    "write_file",
    "bash",
    "glob",
    "web_search",
    "web_fetch",
    "llm_chat",
]

MODEL_PROVIDERS = [
    "openrouter",
    "kimi",
    "openai",
    "anthropic",
    "deepseek",
    "local",
]

AGENT_TYPES = [
    "agent",
    "worker",
    "team",
]

TEAM_MODES = [
    "sequential",
    "parallel",
    "supervisor",
]


# ============================================================
# 向导主流程
# ============================================================

def run_setup_wizard() -> int:
    """
    运行交互式设置向导

    Returns:
        退出码 (0 成功, 1 失败)
    """
    print("=" * 60)
    print("  agent-compose 交互式设置向导")
    print("=" * 60)

    # 步骤 1: 选择实体类型
    type_idx = _ask_choice(
        "您想创建什么类型的实体？",
        ["Agent (智能体)", "Worker (工作流)", "Team (团队)"],
        default=0,
    )
    entity_type = AGENT_TYPES[type_idx]

    # 步骤 2: 输入基本信息
    print("\n" + "-" * 40)
    name = _ask("名称（英文标识，如 my_agent）")
    if not name:
        print("错误：名称不能为空。", file=sys.stderr)
        return 1

    description = _ask("描述")
    if not description:
        description = f"Auto-generated {entity_type}"

    # 步骤 3: 根据类型收集特定信息
    if entity_type == "agent":
        return _wizard_agent(name, description)
    elif entity_type == "worker":
        return _wizard_worker(name, description)
    elif entity_type == "team":
        return _wizard_team(name, description)

    return 1


def _wizard_agent(name: str, description: str) -> int:
    """Agent 创建向导子流程"""
    # 选择工具
    tools = _ask_multiselect(
        "选择该 Agent 需要的工具（可多选）：",
        AVAILABLE_TOOLS,
    )

    # 选择模型提供商
    provider_idx = _ask_choice(
        "选择模型提供商：",
        MODEL_PROVIDERS,
        default=0,
    )
    model_provider = MODEL_PROVIDERS[provider_idx]

    # 模型 ID
    default_model_id = {
        "openrouter": "openrouter/free",
        "kimi": "moonshot-v1-128k",
        "openai": "gpt-4o",
        "anthropic": "claude-3-5-sonnet",
        "deepseek": "deepseek-chat",
        "local": "llama3",
    }.get(model_provider, "openrouter/free")

    model_id = _ask("模型 ID", default=default_model_id)

    # 生成模板
    template = generate_agent_template(
        name=name,
        description=description,
        tools=tools,
        model_provider=model_provider,
        model_id=model_id,
    )

    # 确认输出路径
    default_output = os.path.join(".", name, "agent.json")
    output_path = _ask("输出文件路径", default=default_output)

    # 显示摘要
    print("\n" + "=" * 60)
    print("  配置摘要")
    print("=" * 60)
    print(f"  类型:       Agent")
    print(f"  名称:       {name}")
    print(f"  描述:       {description}")
    print(f"  工具:       {', '.join(tools) if tools else '无'}")
    print(f"  模型:       {model_provider}/{model_id}")
    print(f"  输出路径:   {output_path}")
    print("=" * 60)

    if not _ask_yes_no("确认生成？", default=True):
        print("已取消。")
        return 0

    # 写入文件
    try:
        written = write_template_to_file(template, output_path)
        print(f"\n✅ 已成功生成 Agent 配置: {written}")
    except Exception as e:
        print(f"\n❌ 生成失败: {e}", file=sys.stderr)
        return 1

    return 0


def _wizard_worker(name: str, description: str) -> int:
    """Worker 创建向导子流程"""
    agent_name = _ask("关联的 Agent 名称", default=name)

    steps: List[Dict[str, Any]] = []
    print("\n配置工作步骤（至少一个）：")
    while True:
        step_name = _ask(f"步骤 {len(steps) + 1} 名称", default=f"step_{len(steps) + 1}")
        step_prompt = _ask("步骤提示词", default="Execute the task")
        steps.append({
            "name": step_name,
            "agent": agent_name,
            "prompt": step_prompt,
        })
        if not _ask_yes_no("是否添加更多步骤？", default=False):
            break

    template = generate_worker_template(agent_name=agent_name, steps=steps)

    default_output = os.path.join(".", name, "worker.yaml")
    output_path = _ask("输出文件路径", default=default_output)

    print("\n" + "=" * 60)
    print("  配置摘要")
    print("=" * 60)
    print(f"  类型:       Worker")
    print(f"  名称:       {name}")
    print(f"  描述:       {description}")
    print(f"  关联 Agent: {agent_name}")
    print(f"  步骤数:     {len(steps)}")
    for i, step in enumerate(steps, 1):
        print(f"    [{i}] {step['name']}: {step['prompt']}")
    print(f"  输出路径:   {output_path}")
    print("=" * 60)

    if not _ask_yes_no("确认生成？", default=True):
        print("已取消。")
        return 0

    try:
        written = write_template_to_file(template, output_path)
        print(f"\n✅ 已成功生成 Worker 配置: {written}")
    except Exception as e:
        print(f"\n❌ 生成失败: {e}", file=sys.stderr)
        return 1

    return 0


def _wizard_team(name: str, description: str) -> int:
    """Team 创建向导子流程"""
    mode_idx = _ask_choice(
        "选择团队协作模式：",
        ["Sequential (顺序执行)", "Parallel (并行执行)", "Supervisor (监督模式)"],
        default=0,
    )
    mode = TEAM_MODES[mode_idx]

    members: List[Dict[str, Any]] = []
    print("\n配置团队成员（至少一个）：")
    while True:
        member_name = _ask(f"成员 {len(members) + 1} 名称")
        if not member_name:
            if len(members) == 0:
                print("错误：至少需要一名成员。", file=sys.stderr)
                continue
            break
        member_role = _ask("角色", default="member")
        member_agent = _ask("关联 Agent 名称", default=member_name)
        members.append({
            "name": member_name,
            "role": member_role,
            "agent": member_agent,
        })
        if not _ask_yes_no("是否添加更多成员？", default=False):
            break

    template = generate_team_template(name=name, members=members, mode=mode)

    default_output = os.path.join(".", name, "team.yaml")
    output_path = _ask("输出文件路径", default=default_output)

    print("\n" + "=" * 60)
    print("  配置摘要")
    print("=" * 60)
    print(f"  类型:       Team")
    print(f"  名称:       {name}")
    print(f"  描述:       {description}")
    print(f"  模式:       {mode}")
    print(f"  成员数:     {len(members)}")
    for i, m in enumerate(members, 1):
        print(f"    [{i}] {m['name']} ({m['role']}) -> Agent: {m['agent']}")
    print(f"  输出路径:   {output_path}")
    print("=" * 60)

    if not _ask_yes_no("确认生成？", default=True):
        print("已取消。")
        return 0

    try:
        written = write_template_to_file(template, output_path)
        print(f"\n✅ 已成功生成 Team 配置: {written}")
    except Exception as e:
        print(f"\n❌ 生成失败: {e}", file=sys.stderr)
        return 1

    return 0


# ============================================================
# 预置模板快速使用
# ============================================================

def run_template_wizard(template_id: str) -> int:
    """
    基于预置模板快速生成 Agent

    Args:
        template_id: 预置模板 ID

    Returns:
        退出码
    """
    template = get_builtin_template(template_id)
    if template is None:
        print(f"❌ 未知模板: '{template_id}'", file=sys.stderr)
        print("\n可用模板：")
        for info in list_builtin_templates():
            print(f"  - {info['id']}: {info['name']} ({info['description']})")
        return 1

    print(f"\n🎨 使用模板: {template['identity']['display_name']}")

    # 询问自定义名称
    default_name = template["identity"]["name"]
    name = _ask("Agent 名称", default=default_name)
    if name != default_name:
        template["identity"]["name"] = name
        template["identity"]["display_name"] = name.replace("_", " ").title()

    # 询问自定义描述
    default_desc = template["identity"]["description"]
    desc = _ask("描述", default=default_desc)
    template["identity"]["description"] = desc

    # 输出路径
    default_output = os.path.join(".", name, "agent.json")
    output_path = _ask("输出文件路径", default=default_output)

    print("\n" + "=" * 60)
    print("  配置摘要")
    print("=" * 60)
    print(f"  模板:       {template_id}")
    print(f"  名称:       {template['identity']['name']}")
    print(f"  描述:       {template['identity']['description']}")
    print(f"  标签:       {', '.join(template['identity'].get('tags', []))}")
    print(f"  输出路径:   {output_path}")
    print("=" * 60)

    if not _ask_yes_no("确认生成？", default=True):
        print("已取消。")
        return 0

    try:
        written = write_template_to_file(template, output_path)
        print(f"\n✅ 已成功生成 Agent 配置: {written}")
    except Exception as e:
        print(f"\n❌ 生成失败: {e}", file=sys.stderr)
        return 1

    return 0

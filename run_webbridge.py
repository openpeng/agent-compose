"""
Kimi WebBridge 端到端验证脚本

使用方法：

1. 安装 Kimi WebBridge Chrome/Edge 浏览器插件
2. 在插件中启动本地服务，获取端口号和 token
3. 设置环境变量：
   set KIMI_API_KEY=sk-xxx
   set WEBBRIDGE_TOKEN=your_token
4. 修改 definitions/skills.yml 中的端口号
5. 运行：python run_webbridge.py
"""

import os
import sys
import json
from pathlib import Path

# 将项目根目录加入 path
BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from agent_compose.agent_runner import AgentRunner
from agent_compose.packagers.agent_packager import AgentPackager
from agent_compose.orchestrator import YamlOrchestrator


def run_full_demo():
    print("\n" + "=" * 70)
    print(" Kimi WebBridge Agent - 端到端验证")
    print("=" * 70)

    project_dir = BASE_DIR / "examples" / "kimi_webbridge"

    # ---- 步骤 1: 验证配置
    print("\n[验证1: 读取 YAML 配置")
    orch = YamlOrchestrator(project_dir=str(project_dir))
    agent = orch.get_agent("webbridge_agent")

    if agent is None:
        print("  ✗ 找不到 agent 配置失败")
        return 1

    model = agent.get("model", {})
    print(f"  ✓ 角色: {agent.get('role', '')}")
    print(f"  ✓ 模型: {model.get('provider', 'kimi')} / {model.get('id', '')}")
    tools = agent.get("tools", [])
    if isinstance(tools, dict):
        print(f"  ✓ MCP 工具: {tools.get('mcps', [])}")
        print(f"  ✓ 内置工具: {tools.get('builtin', [])}")

    # ---- 步骤 2: 打包为 agent.json
    print("\n[验证2: 打包为 agent.json")
    packager = AgentPackager(output_dir=str(project_dir / "dist"))
    path = packager.package("webbridge_agent", agent)
    print(f"  ✓ 已打包到: {path}")

    agent_json_path = Path(path) / "agent.json"
    with open(agent_json_path, encoding="utf-8") as f:
        agent_json = json.load(f)
    agent_json_keys = list(agent_json.keys())[:5]
    print(f"  ✓ Agent JSON Keys: {agent_json_keys}")

    # ---- 步骤 3: 启动 Runner
    print("\n[验证3: 启动 Agent Runner")

    api_key = os.environ.get("KIMI_API_KEY", "")
    webbridge_token = os.environ.get("WEBBRIDGE_TOKEN", "")

    runner = AgentRunner(
        project_dir=str(project_dir),
        agent_name="webbridge_agent",
        api_key=api_key,
        webbridge_token=webbridge_token,
    )

    init_ok = runner.initialize()

    # ---- 步骤 4: 对话演示
    print("\n[验证4: 对话演示]")

    # 即使 MCP 连接失败，也可以演示基础对话
    if not api_key:
        print("  ⚠ 未设置 KIMI_API_KEY，跳过真实对话")
        print("  提示: set KIMI_API_KEY=sk-xxx")
        return 0

    if not webbridge_token:
        print("  ⚠ 未设置 WEBBRIDGE_TOKEN，将以纯文本模式演示")

    # 简单对话
    demo_messages = [
        "你好！请介绍一下你自己。",
        "帮我分析一下 agent-compose 项目的主要功能。",
    ]

    if init_ok and runner.llm and api_key:
        for msg in demo_messages:
            runner.chat(msg)

    print("\n" + "=" * 70)
    print(" 验证完成！")
    print("=" * 70)

    # ---- 步骤 5: 交互模式提示
    print("\n提示：要启动交互式对话，运行:")
    print("  python run_webbridge.py --interactive")
    print("\n要只运行交互式对话不需要真实调用 LLM，并启动交互式对话")

    return 0


def run_interactive():
    project_dir = BASE_DIR / "examples" / "kimi_webbridge"
    api_key = os.environ.get("KIMI_API_KEY", "")
    webbridge_token = os.environ.get("WEBBRIDGE_TOKEN", "")

    runner = AgentRunner(
        project_dir=str(project_dir),
        agent_name="webbridge_agent",
        api_key=api_key,
        webbridge_token=webbridge_token,
    )
    runner.initialize()
    runner.interactive_mode()


def main():
    if "--interactive" in sys.argv or "-i" in sys.argv:
        run_interactive()
    else:
        run_full_demo()


if __name__ == "__main__":
    main()

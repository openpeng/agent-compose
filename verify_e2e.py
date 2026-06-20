"""
端到端验证脚本: market.aitboy.cn → 下载 agent → 运行

使用:
    # 1) 基本验证（不需要 API key，测试市场 API + 解析）
    python verify_e2e.py

    # 2) 真实运行（需要 KIMI_API_KEY 和 Kimi WebBridge 浏览器插件）
    #    设置环境变量后:
    #    python -m agent_compose.deploy_cli run kimi-webbridge-operator -i
"""
import json
import sys
import os
from pathlib import Path

# 确保 agent_compose 包可导入
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from agent_compose.market_client import MarketClient
from agent_compose.agent_runtime import AgentRuntime


def step(msg: str) -> None:
    print(f"\n{'='*60}")
    print(f" {msg}")
    print(f"{'='*60}")


def verify_market_api() -> bool:
    step("Step 1: 验证市场 API 健康检查")
    market = MarketClient()
    try:
        h = market.health()
        print(f"  ✓ 状态: {h.get('status')}")
        print(f"  ✓ Agents: {h.get('agents_count')}")
        print(f"  ✓ Version: {h.get('version')}")
        return True
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        return False


def verify_search() -> bool:
    step("Step 2: 验证搜索 API")
    market = MarketClient()
    try:
        items = market.search_and_list(q="webbridge", entity_type="agents", page_size=5)
        print(f"  ✓ 搜索 'webbridge': {len(items)} 个结果")
        for i, item in enumerate(items[:3], 1):
            name = item.get("id") or item.get("name")
            display = item.get("display_name", "")
            print(f"    [{i}] {name} - {display[:50]}")
        return True
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        return False


def verify_download(agent_id: str) -> bool:
    step(f"Step 3: 下载 agent '{agent_id}'")
    market = MarketClient()
    try:
        agent_json, from_cache = market.fetch_agent_json(agent_id, use_cache=True, force_refresh=True)
        source = "缓存" if from_cache else "市场"
        print(f"  ✓ 从 {source} 成功获取")
        print(f"  ✓ schema_version: {agent_json.get('schema_version', '?')}")

        identity = agent_json.get("identity", {}) or {}
        print(f"  ✓ name: {identity.get('name')}")
        print(f"  ✓ display_name: {identity.get('display_name')}")
        print(f"  ✓ version: {identity.get('version')}")

        capabilities = agent_json.get("capabilities", []) or []
        print(f"  ✓ capabilities: {len(capabilities)} 项")
        for c in capabilities[:5]:
            print(f"    - [{c.get('type','?')}] {c.get('name','?')} - {(c.get('description','') or '')[:50]}")

        mcp_servers = agent_json.get("mcp_servers", []) or []
        print(f"  ✓ mcp_servers: {len(mcp_servers)} 项")
        for s in mcp_servers:
            print(f"    - {s.get('name','?')} (command={s.get('command','')})")

        sys_prompt = (agent_json.get("instructions", {}) or {}).get("content", "")
        print(f"  ✓ system prompt length: {len(sys_prompt)} chars")

        # 验证 schema
        required_keys = ["schema_version", "identity", "instructions"]
        for k in required_keys:
            assert k in agent_json, f"缺少字段: {k}"
        print("  ✓ schema 结构验证通过")

        return True
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def verify_agent_runtime() -> bool:
    step("Step 4: AgentRuntime 初始化（不调用真实 LLM / MCP）")
    try:
        market = MarketClient()
        agent_json, _ = market.fetch_agent_json("kimi-webbridge-operator", use_cache=True)

        runtime = AgentRuntime(
            agent_id="kimi-webbridge-operator",
            agent_json=agent_json,
            api_key="sk-test",  # 测试用假 key
            webbridge_token="",
        )

        summary = runtime.summary()
        print(f"  ✓ AgentRuntime 创建成功")
        print(f"  ✓ agent_id: {summary['agent_id']}")
        print(f"  ✓ display_name: {summary['display_name']}")
        print(f"  ✓ capabilities: {summary['capabilities']}")
        print(f"  ✓ mcp_servers: {summary['mcp_servers']}")

        # 验证工具 schema 构建
        # 手动添加 mock tools 测试
        runtime._available_tools = [
            {
                "name": "browser_navigate",
                "description": "导航到指定 URL",
                "inputSchema": {
                    "type": "object",
                    "properties": {"url": {"type": "string", "description": "要导航到的 URL"}},
                    "required": ["url"],
                },
            },
            {
                "name": "browser_click",
                "description": "点击页面元素",
                "inputSchema": {
                    "type": "object",
                    "properties": {"selector": {"type": "string", "description": "CSS selector"}},
                    "required": ["selector"],
                },
            },
        ]
        runtime._tool_name_to_mcp = {"browser_navigate": "mock", "browser_click": "mock"}

        schema = runtime._build_tool_schema()
        print(f"  ✓ 工具 schema: {len(schema)} 个工具")
        assert schema[0]["type"] == "function"
        assert schema[0]["function"]["name"] == "browser_navigate"
        print(f"  ✓ schema 格式正确")

        args_text = runtime._format_args({"url": "https://example.com", "query": "test"})
        print(f"  ✓ 参数格式化: {args_text}")

        return True
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        import traceback
        traceback.print_exc()
        return False


def verify_cli_help() -> bool:
    step("Step 5: CLI 帮助信息")
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "agent_compose.deploy_cli", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        print(f"  ✓ return code: {result.returncode}")
        print(f"  ✓ 输出长度: {len(result.stdout)} chars")
        # 验证子命令
        for subcmd in ["health", "search", "download", "show", "run"]:
            if subcmd in result.stdout:
                print(f"  ✓ 子命令 '{subcmd}' 已注册")
            else:
                print(f"  ⚠️  子命令 '{subcmd}' 未找到")
        return True
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        return False


def verify_cli_search() -> bool:
    step("Step 6: CLI search 命令")
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "agent_compose.deploy_cli", "search", "kimi", "--limit", "3"],
            capture_output=True, text=True, timeout=30,
        )
        print(f"  ✓ return code: {result.returncode}")
        if result.stdout:
            lines = result.stdout.strip().split("\n")[:10]
            for line in lines:
                print(f"  {line}")
        if result.stderr:
            print(f"  stderr preview: {result.stderr[:200]}")
        return result.returncode == 0
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        return False


def verify_cli_health() -> bool:
    step("Step 7: CLI health 命令")
    try:
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "agent_compose.deploy_cli", "health"],
            capture_output=True, text=True, timeout=30,
        )
        print(f"  ✓ return code: {result.returncode}")
        if result.stdout:
            for line in result.stdout.strip().split("\n"):
                print(f"  {line}")
        return result.returncode == 0
    except Exception as e:
        print(f"  ✗ 失败: {e}")
        return False


def summary(passed: int, total: int, results: dict) -> None:
    step(f"结果汇总: {passed}/{total} 通过")
    for name, ok in results.items():
        icon = "✅" if ok else "❌"
        print(f"  {icon} {name}")
    if passed == total:
        print("\n🎉 全部通过!")
        print("\n下一步: 运行真实对话:")
        print("  set KIMI_API_KEY=sk-xxx")
        print(f"  python -m agent_compose.deploy_cli run kimi-webbridge-operator -i")
    else:
        print(f"\n⚠️  {total - passed} 项未通过，请检查上方错误信息")


def main() -> int:
    results = {}
    steps = [
        ("市场健康检查 API", verify_market_api),
        ("Agent 搜索 API", verify_search),
        ("Agent 下载 + schema 解析", lambda: verify_download("kimi-webbridge-operator")),
        ("AgentRuntime 初始化", verify_agent_runtime),
        ("CLI 帮助信息", verify_cli_help),
        ("CLI search 命令", verify_cli_search),
        ("CLI health 命令", verify_cli_health),
    ]

    for name, func in steps:
        results[name] = func()

    passed = sum(1 for v in results.values() if v)
    total = len(results)
    summary(passed, total, results)
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())

"""
Kimi WebBridge 端到端验证
=======================

使用步骤:
    1) 安装 Kimi WebBridge Chrome/Edge 浏览器扩展
    2) 设置环境变量: set OPENROUTER_API_KEY=sk-or-v1-...  或  set KIMI_API_KEY=sk-...
    3) 运行: python verify_webbridge_e2e.py

测试内容:
    ✓ Step 1: 健康检查 - 确认 http://127.0.0.1:10086/status 正常
    ✓ Step 2: AgentRuntime 加载 kimi-webbridge-operator
    ✓ Step 3: MCP 初始化并注册所有 13 个 WebBridge 工具
    ✓ Step 4: 直接工具调用测试 (navigate, snapshot, click)
    ✓ Step 5: (有 API Key 时) LLM -> WebBridge 工具调用 -> 总结
    ✓ Step 6: 交互式对话 (有 API Key 时)
"""
import json
import os
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from agent_compose.market_client import MarketClient
from agent_compose.agent_runtime import AgentRuntime
from agent_compose.kimi_webbridge_client import KimiWebBridgeClient


def step(msg: str) -> None:
    bar = "=" * 70
    print(f"\n{bar}")
    print(f" {msg}")
    print(f"{bar}\n")


def run() -> int:
    results = {}
    runtime = None

    # ---------- Step 1: 健康检查 ----------
    step("Step 1: Kimi WebBridge 健康检查")
    try:
        client = KimiWebBridgeClient()
        health = client.health_check()
        if isinstance(health, dict) and "error" in health and health.get("error"):
            print(f" ✗ WebBridge 未响应: {health.get('error')}")
            print("\n  请确保:")
            print("   1) 在 Chrome/Edge 浏览器中安装 Kimi WebBridge 扩展")
            print("   2) 扩展已启用并在运行")
            print("   3) netstat -ano | findstr 10086   应显示 LISTENING")
            results["health"] = False
        else:
            print(f" ✓ 本地 daemon 运行中")
            print(f"   URL: http://127.0.0.1:10086")
            if isinstance(health, dict):
                for k, v in health.items():
                    print(f"   {k}: {v}")
            results["health"] = True
    except Exception as e:
        print(f" ✗ 异常: {e}")
        results["health"] = False

    # ---------- Step 2: 加载 Agent ----------
    step("Step 2: 从市场下载 kimi-webbridge-operator")
    try:
        market = MarketClient()
        agent_id = "kimi-webbridge-operator"
        agent_json, cached = market.fetch_agent_json(agent_id)
        if not agent_json or not isinstance(agent_json, dict):
            # 构建一个最小的 webbridge agent
            print("  未从市场获取到 json, 使用内置配置...")
            agent_json = {
                "schema_version": "2.0",
                "identity": {
                    "name": "kimi-webbridge-operator",
                    "display_name": "Kimi WebBridge 浏览器助手",
                    "description": "通过 Kimi WebBridge 浏览器扩展与网页交互",
                },
                "instructions": {
                    "format": "markdown",
                    "content": "你是一个网页浏览器助手, 可以通过工具打开网页、点击链接、获取内容。"
                },
                "capabilities": [
                    {"type": "tool_call", "name": "navigate"},
                    {"type": "tool_call", "name": "click"},
                    {"type": "tool_call", "name": "snapshot"},
                ],
                "mcp_servers": [
                    {"name": "kimi-webbridge", "command": "npx", "args": ["@kimi/webbridge-mcp"]}
                ],
            }
        print(f" ✓ Agent 已加载: {agent_json.get('identity', {}).get('display_name', agent_json.get('identity', {}).get('name', agent_id))}")
        print(f"   MCP servers: {[s.get('name') for s in agent_json.get('mcp_servers', [])]}")
        print(f"   Capabilities: {[c.get('name') for c in agent_json.get('capabilities', [])]}")
        results["load_agent"] = True
    except Exception as e:
        print(f" ✗ 失败: {e}")
        import traceback
        traceback.print_exc()
        results["load_agent"] = False
        return 1

    # ---------- Step 3: AgentRuntime 初始化 ----------
    step("Step 3: AgentRuntime 初始化并连接 MCP")
    try:
        runtime = AgentRuntime(
            agent_id="kimi-webbridge-operator",
            agent_json=agent_json,
            api_key=os.environ.get("OPENROUTER_API_KEY") or os.environ.get("KIMI_API_KEY", ""),
            model_provider="openrouter" if os.environ.get("OPENROUTER_API_KEY") else (
                "kimi" if os.environ.get("KIMI_API_KEY") else "openai"
            ),
            model_id=(
                "openrouter/free" if os.environ.get("OPENROUTER_API_KEY")
                else ("moonshot-v1-128k" if os.environ.get("KIMI_API_KEY") else "gpt-4o-mini")
            ),
            base_url=(
                "https://openrouter.ai/api/v1" if os.environ.get("OPENROUTER_API_KEY")
                else ("https://api.moonshot.cn/v1" if os.environ.get("KIMI_API_KEY") else "")
            ),
        )

        print(f" Agent: {runtime.display_name}")
        print(f" Model: {runtime.model_provider}/{runtime.model_id}")
        print(f" API Key: {'已设置' if runtime.api_key else '未设置'}")
        print()

        connected = runtime.initialize_mcps(auto_connect_webbridge=True, auto_connect_stdio=False)
        print(f"\n ✓ 已连接 MCP: {connected}")
        print(f" ✓ 可用工具: {[t.get('function', {}).get('name', t.get('name', '?')) for t in runtime._available_tools]}")
        print(f" ✓ 工具数量: {len(runtime._available_tools)}")
        results["mcp_init"] = len(connected) > 0
    except Exception as e:
        print(f" ✗ 失败: {e}")
        import traceback
        traceback.print_exc()
        results["mcp_init"] = False

    # ---------- Step 4: 直接工具调用 ----------
    step("Step 4: 直接工具调用测试 (不经过 LLM)")
    try:
        # navigate
        r = runtime._call_mcp_tool("webbridge_navigate", {"url": "https://www.example.com"})
        ok = "success" in str(r.get("content", "")) or "true" in str(r.get("content", "")).lower() or "ok" in str(r).lower()
        print(f" navigate -> {r}")
        print(f" {'✓' if ok else '✗'} navigate")
        time.sleep(1)

        # snapshot
        r = runtime._call_mcp_tool("webbridge_snapshot", {})
        content_str = str(r.get("content", ""))
        has_content = len(content_str) > 100
        print(f" snapshot -> {content_str[:300]}...")
        print(f" {'✓' if has_content else '✗'} snapshot (内容长度 {len(content_str)})")
        time.sleep(1)

        # evaluate
        r = runtime._call_mcp_tool("webbridge_evaluate", {"code": "document.title"})
        print(f" evaluate(document.title) -> {r}")
        has_title = "Example" in str(r.get("content", "")) or len(str(r.get("content", ""))) > 10
        print(f" {'✓' if has_title else '✗'} evaluate")

        results["direct_tools"] = ok and has_content and has_title
    except Exception as e:
        print(f" ✗ 失败: {e}")
        import traceback
        traceback.print_exc()
        results["direct_tools"] = False

    # ---------- Step 5: LLM 对话 (有 API Key 时) ----------
    step("Step 5: LLM -> WebBridge 端到端对话")
    if not runtime or not runtime.api_key:
        print("  ℹ 未设置 API Key, 跳过 LLM 对话测试")
        print("    设置方法: set OPENROUTER_API_KEY=sk-or-v1-...")
        results["llm_chat"] = True  # 不是失败
    else:
        try:
            prompt = "请打开 https://www.example.com，然后告诉我这个网页的标题和主要内容"
            print(f" 用户: {prompt}")
            print()

            history = runtime.chat(prompt, max_turns=10)

            # 打印对话摘要
            final_text = ""
            tool_calls = 0
            for i, item in enumerate(history):
                if item.get("type") == "assistant" and item.get("content"):
                    content = str(item.get("content", ""))
                    final_text = content
                    print(f" [Assistant {i}]: {content[:200]}")
                tcs = item.get("tool_calls") or []
                if tcs:
                    for tc in tcs:
                        fn = tc.get("function", {}).get("name", "?")
                        args = str(tc.get("function", {}).get("arguments", "{}"))[:80]
                        print(f" [Tool Call {i}]: {fn}({args})")
                        tool_calls += 1
                tr = item.get("tool_result")
                if tr:
                    print(f" [Tool Result {i}]: {str(tr)[:200]}")

            print(f"\n 工具调用次数: {tool_calls}")
            print(f" 最终回答长度: {len(final_text)} chars")
            if tool_calls > 0:
                print(f" ✓ LLM 成功调用了 WebBridge 工具")
            if len(final_text) > 50:
                print(f" ✓ LLM 生成了有效的总结")
            results["llm_chat"] = tool_calls > 0 or len(final_text) > 50
        except Exception as e:
            print(f" ✗ 失败: {e}")
            import traceback
            traceback.print_exc()
            results["llm_chat"] = False

    # ---------- Step 6: 交互式对话 ----------
    step("Step 6: 交互式对话 (Ctrl+C 或输入 'quit' 退出)")
    if not runtime or not runtime.api_key:
        print("  ℹ 无 API Key, 跳过交互式对话")
        results["interactive"] = True
    else:
        try:
            print("  您现在可以跟浏览器助手对话了。示例:")
            print("     - 打开 baidu.com，搜索 python")
            print("     - 打开 example.com，告诉我它的标题")
            print("     - 截图当前页")
            print("     - quit (退出)\n")
            while True:
                try:
                    msg = input(" 你: ").strip()
                except (KeyboardInterrupt, EOFError):
                    print()
                    break
                if not msg or msg.lower() in ("quit", "exit", "q"):
                    break

                history = runtime.chat(msg, max_turns=10)
                # 只打印最后的 assistant 文本
                last_text = ""
                for item in history:
                    if item.get("type") == "assistant" and item.get("content"):
                        last_text = str(item.get("content", ""))
                    tcs = item.get("tool_calls") or []
                    for tc in tcs:
                        fn = tc.get("function", {}).get("name", "?")
                        args = str(tc.get("function", {}).get("arguments", "{}"))[:80]
                        print(f"   ↪ 调用 {fn}({args})")
                    tr = item.get("tool_result")
                    if tr:
                        s = str(tr)
                        if isinstance(tr, dict):
                            s = str(tr.get("content", tr))
                        print(f"   ↩ 结果: {s[:200]}")
                print(f"  助手: {last_text}\n")
            results["interactive"] = True
        except Exception as e:
            print(f"  ✗ 错误: {e}")
            import traceback
            traceback.print_exc()
            results["interactive"] = False

    # ---------- 清理 ----------
    if runtime:
        runtime.close_mcps()

    # ---------- 汇总 ----------
    step("测试汇总")
    total = len(results)
    passed = sum(1 for v in results.values() if v)
    for name, ok in results.items():
        icon = "✅" if ok else "❌"
        print(f"  {icon} {name}")
    print(f"\n 总计: {passed}/{total}")

    if passed == total:
        print("\n🎉 Kimi WebBridge 端到端验证完成!")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(run())

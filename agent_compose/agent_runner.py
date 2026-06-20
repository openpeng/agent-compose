"""Agent Runner - 真正执行 Agent 对话的引擎

整合以下组件：
1. YamlOrchestrator - 从 YAML 加载 Agent 配置
2. LLMClient - 调用 LLM API 进行对话
3. MCPSSEClient - 连接 MCP Server 获取浏览器操作工具

工作流程：
1. 从 YAML 加载 Agent 配置
2. 识别 MCP 工具并连接到 MCP Server
3. 从 MCP Server 获取实际工具列表
4. 将工具注册到 LLM
5. 与用户对话，LLM 决定调用哪些工具
6. 执行工具并将结果返回给 LLM 继续对话
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent_compose.orchestrator import YamlOrchestrator
from agent_compose.llm_client import LLMClient
from agent_compose.mcp_sse_client import MCPSSEClient


def _format_mcp_tools_schema(mcp_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """将 MCP 工具列表转换为 LLM 可识别的工具 schema"""
    formatted = []
    for tool in mcp_tools:
        name = tool.get("name", "")
        if not name:
            continue
        description = tool.get("description", "")
        input_schema = tool.get("inputSchema", {}) or {}

        if isinstance(input_schema, dict) and "properties" in input_schema:
            properties = input_schema["properties"]
            required = input_schema.get("required", [])
        else:
            properties = {"arg": {"type": "string", "description": "参数"}}
            required = []

        formatted.append({
            "type": "function",
            "function": {
                "name": name,
                "description": description or f"Call {name} tool",
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        })
    return formatted


class AgentRunner:
    """Agent 执行引擎

    使用方法：
        runner = AgentRunner(project_dir="path/to/project", agent_name="webbridge_agent")
        runner.initialize()
        response = runner.chat("帮我打开 www.baidu.com 并搜索 agent-compose")
        print(response["content"])
    """

    def __init__(
        self,
        project_dir: str,
        agent_name: str,
        api_key: Optional[str] = None,
        webbridge_token: Optional[str] = None,
        max_tool_iterations: int = 10,
    ):
        self.project_dir = Path(project_dir)
        self.agent_name = agent_name
        self.max_tool_iterations = max_tool_iterations

        self.orchestrator = YamlOrchestrator(project_dir=str(self.project_dir))
        self.agent_config: Optional[Dict[str, Any]] = None
        self.llm: Optional[LLMClient] = None
        self.mcp_clients: Dict[str, MCPSSEClient] = {}
        self.available_tools: List[Dict[str, Any]] = []
        self.tool_schema: List[Dict[str, Any]] = []

        self._api_key = api_key
        self._webbridge_token = webbridge_token
        self._initialized = False

    # ---------- 初始化 ----------

    def initialize(self) -> bool:
        """初始化：加载配置、连接 MCP、准备 LLM"""
        print(f"\n{'=' * 60}")
        print(f"[AgentRunner] 初始化 Agent: {self.agent_name}")
        print(f"{'=' * 60}")

        # 1. 加载 Agent 配置
        print("\n[步骤1] 加载 YAML 配置...")
        self.agent_config = self.orchestrator.get_agent(self.agent_name)
        if self.agent_config is None:
            print(f"  ✗ 未找到 Agent '{self.agent_name}'")
            return False
        print(f"  ✓ 角色: {self.agent_config.get('role', '')}")
        print(f"  ✓ 描述: {self.agent_config.get('description', '')}")
        print(f"  ✓ 指令: {len(self.agent_config.get('instructions', []))} 条")

        # 2. 配置 LLM
        print("\n[步骤2] 配置 LLM API...")
        model_config = self.agent_config.get("model", {})
        provider = model_config.get("provider", "kimi")
        model_id = model_config.get("id", "")
        base_url = model_config.get("params", {}).get("base_url", "")
        raw_api_key = model_config.get("api_key", "")

        # 解析 API Key（如果是 ${VAR} 格式）
        env_api_key = self._api_key or os.environ.get(f"{provider.upper()}_API_KEY", "")
        if raw_api_key and raw_api_key.startswith("${") and raw_api_key.endswith("}"):
            env_name = raw_api_key[2:-1]
            if ":-" in env_name:
                env_name, default = env_name.split(":-", 1)
                api_key = os.environ.get(env_name, default)
            else:
                api_key = os.environ.get(env_name, "")
        elif raw_api_key:
            api_key = raw_api_key
        else:
            api_key = env_api_key

        if not api_key:
            print(f"  ⚠ 未设置 API Key (环境变量 {provider.upper()}_API_KEY)")
        else:
            print(f"  ✓ API Key 已设置 (以 {api_key[:12]}...)")

        self.llm = LLMClient(
            provider=provider,
            api_key=api_key,
            model=model_id,
            base_url=base_url,
        )
        print(f"  ✓ Provider: {provider}")
        print(f"  ✓ Model: {self.llm.model}")
        print(f"  ✓ Base URL: {self.llm.base_url}")

        # 3. 识别并连接 MCP Server
        print("\n[步骤3] 连接 MCP Server...")
        tools = self.agent_config.get("tools", []) or []
        mcp_tools = [t for t in tools if isinstance(t, dict) and t.get("type") == "mcp"]

        if not mcp_tools:
            print("  ⚠ 未配置 MCP 工具（仅使用内置工具）")
        else:
            for mcp_tool in mcp_tools:
                mcp_name = mcp_tool.get("name", "unknown_mcp")
                mcp_config = mcp_tool.get("config", {}) or {}
                mcp_type = mcp_config.get("type", "sse")

                if mcp_type == "sse":
                    url = mcp_config.get("url", "")

                    # 处理 auth_token（可能是 ${VAR} 格式）
                    raw_token = mcp_config.get("auth_token", "")
                    token = self._webbridge_token or self._resolve_env_var(raw_token)

                    print(f"  → 连接 SSE MCP: {mcp_name}")
                    print(f"    URL: {url}")
                    print(f"    Token: {'已设置' if token else '未设置'}")

                    client = MCPSSEClient(sse_url=url, auth_token=token)
                    connected = client.connect()
                    if connected:
                        server_tools = client.list_tools()
                        if server_tools:
                            self.mcp_clients[mcp_name] = client
                            self.available_tools.extend(server_tools)
                            print(f"  ✓ 连接成功，获取 {len(server_tools)} 个工具:")
                            for t in server_tools[:10]:
                                print(f"    - {t.get('name', '?')}")
                        else:
                            print(f"  ⚠ 连接成功但未获取到工具")
                    else:
                        print(f"  ✗ 连接失败（请确认 MCP Server 已启动）")
                else:
                    print(f"  ⚠ MCP 类型 {mcp_type} 暂不支持实时连接")

        # 4. 添加内置工具信息（仅供展示）
        builtin_tools = [t for t in tools if isinstance(t, dict) and t.get("type") == "builtin"]
        if builtin_tools:
            self.available_tools.extend(builtin_tools)
            print(f"  ✓ 内置工具: {len(builtin_tools)} 个")

        # 5. 构建工具 schema
        if self.available_tools:
            # 过滤出有具体工具名的 MCP 工具（不是 MCP 定义本身）
            runnable_tools = [
                t for t in self.available_tools
                if t.get("inputSchema") or t.get("type") == "builtin"
            ]
            if runnable_tools:
                self.tool_schema = _format_mcp_tools_schema(runnable_tools)
                print(f"\n  ✓ 可调用工具: {len(self.tool_schema)} 个")
            else:
                self.tool_schema = []

        # 6. 完成
        print(f"\n{'=' * 60}")
        print(f"[AgentRunner] 初始化完成 ✓")
        if self.tool_schema:
            print(f"工具列表: {', '.join(t['function']['name'] for t in self.tool_schema)}")
        print(f"{'=' * 60}\n")

        self._initialized = True
        return True

    # ---------- 对话接口 ----------

    def chat(self, user_message: str) -> Dict[str, Any]:
        """执行一次用户对话请求（含工具调用循环）"""
        if not self._initialized:
            return {"content": "[错误] Agent 未初始化，请先调用 initialize()", "tool_calls": []}

        if not self.llm:
            return {"content": "[错误] LLM 客户端未配置", "tool_calls": []}

        if not self.llm.api_key:
            print("\n  ⚠ LLM API Key 未设置，无法进行真实对话")
            print(f"  提示: 请设置 {self.llm.provider.upper()}_API_KEY 环境变量")
            return {"content": "[需要 API Key]", "tool_calls": []}

        print(f"\n{'=' * 60}")
        print(f"你: {user_message}")
        print(f"{'=' * 60}")

        # 构建 system message
        instructions = self.agent_config.get("instructions", []) if self.agent_config else []
        if isinstance(instructions, list):
            system_parts = [f"# 角色: {self.agent_config.get('role', '')}" if self.agent_config else ""]
            for inst in instructions:
                system_parts.append(f"- {inst}")
            if self.tool_schema:
                system_parts.append("\n# 可用工具（按需调用）:")
                for tool in self.tool_schema:
                    desc = tool["function"].get("description", "")
                    system_parts.append(f"- {tool['function']['name']}: {desc}")
            system_message = "\n".join(system_parts)
        else:
            system_message = str(instructions)

        # 第一轮对话
        if self.tool_schema:
            response = self.llm.chat_with_tools(
                user_message, self.tool_schema, system_message=system_message
            )
        else:
            response = self.llm.chat(user_message, system_message=system_message)

        # 工具调用循环
        iteration = 0
        all_tool_results: List[Dict[str, Any]] = []

        while response.get("tool_calls") and iteration < self.max_tool_iterations:
            iteration += 1
            tool_calls = response["tool_calls"]
            print(f"\n  [工具调用 #{iteration}] 共 {len(tool_calls)} 个工具调用")

            for tc in tool_calls:
                tc_id = tc.get("id", "")
                tc_function = tc.get("function", {}) if isinstance(tc, dict) else {}
                tc_name = tc_function.get("name", "")
                try:
                    tc_args = json.loads(tc_function.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    tc_args = {}

                print(f"    → {tc_name}({json.dumps(tc_args, ensure_ascii=False)[:100]})")

                # 执行工具
                tool_result = self._execute_tool(tc_name, tc_args)
                result_str = tool_result.get("result", str(tool_result))[:2000]
                print(f"    ← 结果: {result_str[:150]}")

                all_tool_results.append({"name": tc_name, "result": result_str})

                # 提交工具结果给 LLM，继续对话
                response = self.llm.submit_tool_result(tc_id, tc_name, result_str)

        # 输出最终结果
        content = response.get("content", "")
        print(f"\n{'=' * 60}")
        print(f"Agent: {content}")
        print(f"{'=' * 60}\n")

        return {
            "content": content,
            "tool_calls": response.get("tool_calls", []),
            "tool_results": all_tool_results,
            "usage": response.get("usage", {}),
        }

    def interactive_mode(self) -> None:
        """启动交互式对话模式"""
        if not self._initialized:
            print("请先调用 initialize()")
            return

        print("\n" + "=" * 60)
        print("🤖 交互式对话模式（输入 'quit' 退出）")
        print("=" * 60 + "\n")

        while True:
            try:
                user_input = input("你: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                break

            if not user_input:
                continue
            if user_input.lower() in {"quit", "exit", "bye"}:
                print("再见！")
                break
            if user_input.lower() == "reset":
                if self.llm:
                    self.llm.reset()
                print("对话历史已清空")
                continue

            self.chat(user_input)

    # ---------- 内部工具 ----------

    def _execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """执行工具调用"""
        # 1. 优先走 MCP 连接
        for client in self.mcp_clients.values():
            result = client.call_tool(tool_name, arguments)
            if result and "_error" not in str(result):
                return result

        # 2. 内置工具 fallback
        if tool_name == "web_search":
            return self._builtin_web_search(arguments)
        if tool_name == "WebSearch":
            return self._builtin_web_search(arguments)
        if tool_name in {"calculator", "Calculator"}:
            return self._builtin_calculator(arguments)

        return {"_error": f"无法执行工具 {tool_name}", "result": f"[工具未实现: {tool_name}]"}

    # ---------- 辅助方法 ----------

    def _resolve_env_var(self, value: str) -> str:
        """解析 ${VAR} 或 ${VAR:-default} 格式的环境变量"""
        if not value or not isinstance(value, str):
            return value
        if not value.startswith("${") or not value.endswith("}"):
            return value

        inner = value[2:-1]
        if ":-" in inner:
            var_name, default = inner.split(":-", 1)
            return os.environ.get(var_name, default)
        return os.environ.get(inner, "")

    # ---------- 内置工具 ----------

    def _builtin_web_search(self, args: Dict[str, Any]) -> Dict[str, Any]:
        query = args.get("query", args.get("q", "search"))
        return {"result": f"[Web Search] 关于 '{query}' 的搜索结果（模拟）"}

    def _builtin_calculator(self, args: Dict[str, Any]) -> Dict[str, Any]:
        expression = args.get("expression", "0")
        try:
            result = str(eval(expression, {"__builtins__": {}}, {}))
            return {"result": result}
        except Exception as e:
            return {"result": f"计算错误: {e}"}

    def _builtin_file_reader(self, args: Dict[str, Any]) -> Dict[str, Any]:
        filepath = args.get("path", args.get("file", ""))
        if not filepath:
            return {"result": "未指定文件路径"}
        try:
            p = Path(filepath)
            if not p.exists():
                return {"result": f"文件不存在: {filepath}"}
            content = p.read_text(encoding="utf-8", errors="ignore")
            return {"result": content[:5000]}
        except Exception as e:
            return {"result": f"读取失败: {e}"}

    def _builtin_file_writer(self, args: Dict[str, Any]) -> Dict[str, Any]:
        filepath = args.get("path", args.get("file", ""))
        content = args.get("content", "")
        if not filepath:
            return {"result": "未指定文件路径"}
        try:
            p = Path(filepath)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return {"result": f"文件已写入: {filepath} ({len(content)} chars)"}
        except Exception as e:
            return {"result": f"写入失败: {e}"}

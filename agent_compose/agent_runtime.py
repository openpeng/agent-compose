"""
AgentRuntime - 支持 market.aitboy.cn schema_version 2.0 的 Agent 执行引擎

从市场下载的 agent.json 格式:
{
  "schema_version": "2.0",
  "identity": { ... },
  "instructions": { "content": "..." },
  "capabilities": [ { "type": "tool_call", "name": "navigate", ... } ],
  "mcp_servers": [ { "name": "kimi-webbridge", "type": "kimi-webbridge", "base_url": "http://127.0.0.1:10086", ... } ],
  ...
}

执行策略:
1. 对于 Kimi WebBridge 类型的 MCP server（name 含 webbridge，或 type=="kimi-webbridge"）:
   使用 HTTP JSON 连接到 http://127.0.0.1:10086/command
   同时注册 webbridge_* 和 browser_* 两套工具名供 LLM 调用
2. 对于 stdio 类型的 MCP server: 启动子进程并使用 MCP JSON-RPC 协议
3. 对于 SSE 类型的 MCP server: 通过 HTTP SSE 连接
"""
import json
import os
import time
import re
import urllib.request
import urllib.error
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from .market_client import MarketClient
from .kimi_webbridge_client import KimiWebBridgeClient
from .pipeline_engine import PipelineEngine, ExecutionContext, ToolRegistry
from .tools import register_builtin_tools
from .observability import Observability, set_trace_context, TraceContext


class AgentRuntime:
    """从市场下载的 Agent (schema v2) 的执行引擎"""

    def __init__(
        self,
        agent_id: str,
        agent_json: Dict[str, Any],
        api_key: Optional[str] = None,
        model_provider: str = "kimi",
        model_id: str = "moonshot-v1-128k",
        webbridge_token: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.agent_id = agent_id
        self.agent = agent_json
        self.api_key = api_key or os.environ.get("KIMI_API_KEY", "")
        self.model_provider = model_provider
        self.model_id = model_id
        self.base_url = base_url or "https://api.moonshot.cn/v1"
        self.webbridge_token = webbridge_token or os.environ.get("WEBBRIDGE_TOKEN", "")

        # 解析 agent.json
        self.identity = self.agent.get("identity", {}) or {}
        self.name = self.identity.get("name", agent_id)
        self.display_name = self.identity.get("display_name", self.name)
        self.version = self.identity.get("version", "1.0.0")
        self.description = self.identity.get("description", "")

        self.instructions = self.agent.get("instructions", {}) or {}
        self.system_prompt = self.instructions.get("content", "")

        self.capabilities = self.agent.get("capabilities", []) or []
        self.mcp_servers = self.agent.get("mcp_servers", []) or []

        # MCP 客户端（延迟初始化）
        self._mcp_clients = {}  # name -> MCPStdioClient / SSEClient
        self._tool_name_to_mcp = {}  # tool_name -> mcp_name
        self._custom_mcp_servers: List[Dict[str, Any]] = []  # 手动注册的 MCP 配置

        # Kimi WebBridge 客户端（HTTP JSON on 127.0.0.1:10086）
        self._kimi_webbridge: Optional[KimiWebBridgeClient] = None
        self._kimi_webbridge_enabled: bool = False

        # 动态工具 schema
        self._available_tools = []  # 从 MCP 获取的 tools

        # PipelineEngine 相关
        self.pipeline_engine: Optional[PipelineEngine] = None
        self.tool_registry = ToolRegistry()
        register_builtin_tools(self.tool_registry)
        self.observability: Optional[Observability] = None

    # ---------- 信息 ----------

    def summary(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "display_name": self.display_name,
            "version": self.version,
            "description": self.description,
            "capability_count": len(self.capabilities),
            "capabilities": [c.get("name", "") for c in self.capabilities],
            "mcp_servers": [s.get("name", "") for s in self.mcp_servers],
            "model": {"provider": self.model_provider, "id": self.model_id, "base_url": self.base_url},
            "api_key_set": bool(self.api_key),
            "webbridge_token_set": bool(self.webbridge_token),
        }

    # ---------- Kimi WebBridge 注册 ----------

    def add_kimi_webbridge(
        self,
        base_url: str = "http://127.0.0.1:10086",
        token: str = "",
        name: str = "kimi-webbridge",
    ) -> str:
        """注册并连接 Kimi WebBridge。

        - 使用 HTTP JSON on 127.0.0.1:10086/command
        - 自动注册 24 个工具 schema（webbridge_* + browser_* 双命名）
        - 通过 health_check 验证 daemon 和扩展连接状态

        Returns:
            注册名称（如 kimi-webbridge）
        """
        # 去重注册
        if self._kimi_webbridge_enabled and self._kimi_webbridge:
            print(f"  [WebBridge] {name} 已连接，跳过重复注册")
            return name

        client = KimiWebBridgeClient(base_url=base_url, token=token or self.webbridge_token)
        health = client.health_check()

        # 健康检查：只要 health 中 running == False 或者存在显式 error 字段，认为服务不可用
        is_unhealthy = False
        err_msg = ""
        if isinstance(health, dict):
            if health.get("running") is False:
                is_unhealthy = True
                err_msg = health.get("error", "unknown error")
            elif "error" in health and health["error"]:
                is_unhealthy = True
                err_msg = str(health["error"])
        else:
            is_unhealthy = True
            err_msg = f"unexpected response type"

        if is_unhealthy:
            print(f"  [WebBridge] ✗ 无法连接 {base_url}: {err_msg}")
            print("  [WebBridge] ℹ 请确保在 Chrome/Edge 中安装并启用了 Kimi WebBridge 扩展")
            print("  [WebBridge] ℹ netstat -ano | findstr 10086 应显示 LISTENING")
            self._kimi_webbridge_enabled = False
            self._kimi_webbridge = None
            return name

        self._kimi_webbridge = client
        self._kimi_webbridge_enabled = True

        # 注册 WebBridge 工具 schema (webbridge_* + browser_*)
        webbridge_tools = KimiWebBridgeClient.get_tools(include_alt_names=True)
        for t in webbridge_tools:
            tname = t.get("function", {}).get("name", "")
            if tname:
                self._tool_name_to_mcp[tname] = name
                self._available_tools.append(t)

        # 汇总状态信息
        ext_info = []
        if isinstance(health, dict):
            if health.get("extension_version"):
                ext_info.append(f"v{health['extension_version']}")
            if health.get("extension_connected"):
                ext_info.append("browser extension connected")
        status_line = f"({', '.join(ext_info)})" if ext_info else ""
        print(f"  [WebBridge] ✓ 已连接 {name} ({base_url}) {status_line} - {len(webbridge_tools)} tools")
        return name

    # ---------- MCP 注册 API (手动添加自定义 MCP) ----------

    def add_stdio_mcp(
        self,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        name: str = "",
    ) -> str:
        """注册一个 stdio MCP Server，稍后调用 initialize_mcps 时会启动它。"""
        if not name:
            name = f"custom_stdio_{len(self._custom_mcp_servers) + 1}"
        cfg = {
            "name": name,
            "type": "stdio",
            "command": command,
            "args": args or [],
            "env": env or {},
        }
        self._custom_mcp_servers.append(cfg)
        print(f"  [MCP] 已注册 stdio MCP: {name} ({command} {' '.join(args or [])})")
        return name

    def add_sse_mcp(self, url: str, auth_token: str = "", name: str = "") -> str:
        """注册一个 SSE 类型的 MCP Server。"""
        if not name:
            name = f"custom_sse_{len(self._custom_mcp_servers) + 1}"
        cfg = {
            "name": name,
            "type": "sse",
            "url": url,
            "auth_token": auth_token,
        }
        self._custom_mcp_servers.append(cfg)
        print(f"  [MCP] 已注册 SSE MCP: {name} ({url})")
        return name

    def add_mcp_from_config(self, cfg: Dict[str, Any]) -> str:
        """通过配置 dict 注册 MCP，支持 kimi-webbridge, stdio 或 sse。"""
        name = (cfg.get("name", "") or "")
        args_str = " ".join(cfg.get("args", []) or [])
        if "webbridge" in name.lower() or "@kimi/webbridge" in args_str:
            return self.add_kimi_webbridge()
        mtype = cfg.get("type", "stdio")
        if mtype == "stdio":
            return self.add_stdio_mcp(
                command=cfg.get("command", "python"),
                args=cfg.get("args", []),
                env=cfg.get("env", {}),
                name=name,
            )
        elif mtype == "sse":
            return self.add_sse_mcp(
                url=cfg.get("url", ""),
                auth_token=cfg.get("auth_token", ""),
                name=name,
            )
        else:
            raise ValueError(f"不支持的 MCP 类型: {mtype}")

    # ---------- MCP 初始化 ----------

    def _detect_mcp_type(self, mcp_server: Dict[str, Any]) -> str:
        """检测 MCP server 的类型。kimi-webbridge / sse / stdio。"""
        name = (mcp_server.get("name", "") or "").lower()
        command = mcp_server.get("command", "") or ""
        args = mcp_server.get("args", []) or []
        args_str = " ".join(args) if isinstance(args, list) else str(args)
        cfg_type = mcp_server.get("type", "") or ""

        if cfg_type == "kimi-webbridge":
            return "kimi-webbridge"
        if "webbridge" in name:
            return "kimi-webbridge"
        if "@kimi/webbridge" in args_str or "kimi-webbridge" in args_str:
            return "kimi-webbridge"
        if cfg_type == "sse":
            return "sse"
        if command in ("npx", "node", "npm"):
            return "stdio-node"
        if command in ("python", "python3", "python.exe"):
            return "stdio-python"
        return "stdio"

    def initialize_mcps(
        self,
        auto_connect_webbridge: bool = True,
        auto_connect_stdio: bool = True,
    ) -> List[str]:
        """初始化所有 MCP servers（来自 agent.json + 手动注册的 custom MCP）。

        Kimi WebBridge: 使用 HTTP JSON (http://127.0.0.1:10086/command)
        stdio: 使用 subprocess + MCP JSON-RPC
        """
        connected = []

        # 合并 agent.json 中的 MCP + 手动注册的 MCP
        all_mcps = list(self.mcp_servers) + self._custom_mcp_servers

        for mcp in all_mcps:
            name = mcp.get("name", "unknown")
            mcp_type = self._detect_mcp_type(mcp)

            # --- Kimi WebBridge (HTTP JSON on 127.0.0.1:10086) ---
            if mcp_type == "kimi-webbridge":
                if not auto_connect_webbridge:
                    continue
                reg_name = self.add_kimi_webbridge(
                    base_url=mcp.get("url") or "http://127.0.0.1:10086"
                )
                if self._kimi_webbridge_enabled:
                    connected.append(reg_name)

            # --- SSE 类型 ---
            elif mcp_type == "sse" or mcp.get("type") == "sse":
                if not auto_connect_webbridge:
                    continue
                print(f"  [MCP] 尝试连接 '{name}' (SSE) ...")
                try:
                    from .mcp_sse_client import MCPSSEClient
                    sse_url = mcp.get("url") or ""
                    if not sse_url:
                        print(f"  [MCP] ✗ 未提供 SSE URL，跳过")
                        continue
                    client = MCPSSEClient(sse_url=sse_url, auth_token=mcp.get("auth_token", "") or self.webbridge_token)
                    ok = client.connect()
                    if ok:
                        tools = client.list_tools()
                        self._mcp_clients[name] = client
                        for t in tools:
                            tname = t.get("name", "")
                            if tname:
                                self._tool_name_to_mcp[tname] = name
                                self._available_tools.append(t)
                        print(f"  [MCP] ✓ 已连接 '{name}'，可用工具 {len(tools)} 个")
                        connected.append(name)
                    else:
                        print(f"  [MCP] ✗ 连接失败 '{name}'")
                except Exception as e:
                    print(f"  [MCP] ✗ 连接错误 '{name}': {e}")

            # --- stdio 类型 ---
            elif mcp_type in ("stdio", "stdio-python", "stdio-node") or mcp.get("type") == "stdio":
                if not auto_connect_stdio:
                    continue
                command = mcp.get("command", "")
                args_list = mcp.get("args", []) or []
                env_vars = mcp.get("env", {}) or {}

                print(f"  [MCP] 尝试启动 '{name}' (stdio: {command} {' '.join(args_list)}) ...")
                try:
                    from .mcp_stdio_client import MCPStdioClient
                    client = MCPStdioClient(
                        command=command,
                        args=args_list,
                        env=env_vars,
                        name=name,
                        timeout=30,
                    )
                    ok = client.connect()
                    if ok:
                        tools = client.list_tools()
                        self._mcp_clients[name] = client
                        for t in tools:
                            tname = t.get("name", "")
                            if tname:
                                self._tool_name_to_mcp[tname] = name
                                self._available_tools.append(t)
                        print(f"  [MCP] ✓ 已启动 '{name}'，可用工具 {len(tools)} 个")
                        connected.append(name)
                    else:
                        print(f"  [MCP] ✗ 启动失败 '{name}'")
                except Exception as e:
                    print(f"  [MCP] ✗ 启动错误 '{name}': {e}")
                    import traceback
                    traceback.print_exc()
            else:
                print(f"  [MCP] ℹ '{name}' (type={mcp_type}) 需要用户手动配置，已跳过")

        return connected

    def close_mcps(self) -> None:
        """关闭所有已连接的 MCP servers"""
        for name, client in list(self._mcp_clients.items()):
            try:
                if hasattr(client, "close"):
                    client.close()
            except Exception:
                pass
        self._mcp_clients.clear()
        self._kimi_webbridge = None
        self._kimi_webbridge_enabled = False
        self._tool_name_to_mcp.clear()
        self._available_tools = []
        print("  [MCP] 所有 MCP 已关闭")

    # ---------- LLM 工具调用 ----------

    def _build_tool_schema(self) -> List[Dict[str, Any]]:
        """生成 LLM 工具调用 schema（从已注册的 tools 生成）"""
        if not self._available_tools:
            return []

        tools = []
        for t in self._available_tools:
            # 处理 OpenAI 原生格式
            if t.get("type") == "function" and "function" in t:
                tools.append(t)
                continue
            # 处理 MCP 原生格式
            name = t.get("name", "")
            if not name:
                continue
            desc = t.get("description", f"Tool {name}")
            params = t.get("inputSchema") or t.get("parameters") or {
                "type": "object", "properties": {}, "required": []
            }
            tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": desc,
                    "parameters": params,
                },
            })
        return tools

    def _call_mcp_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """调用一个工具。路由优先级：Kimi WebBridge > 其他 MCP servers。

        - 支持 webbridge_* 和 browser_* 两套命名（自动归一化到 WebBridge）
        """
        # 1) Kimi WebBridge (webbridge_* / browser_* tools)
        if self._kimi_webbridge_enabled and self._kimi_webbridge:
            if tool_name.startswith("webbridge_") or tool_name.startswith("browser_"):
                try:
                    result = self._kimi_webbridge.run_tool(tool_name, arguments)
                    return result
                except Exception as e:
                    return {"content": f"[Error] Kimi WebBridge 调用 '{tool_name}' 失败: {e}"}

        # 2) 其他 MCP servers (stdio / SSE)
        mcp_name = self._tool_name_to_mcp.get(tool_name)
        if not mcp_name:
            return {"content": f"[Error] 工具 '{tool_name}' 未在任何已连接的 MCP server 中找到"}

        client = self._mcp_clients.get(mcp_name)
        if not client:
            return {"content": f"[Error] MCP client for '{mcp_name}' 未初始化"}

        try:
            if hasattr(client, "call_tool"):
                raw = client.call_tool(tool_name, arguments)
            elif hasattr(client, "list_tools"):
                # 兜底：有些 MCP 客户端暴露 list_tools 但没有统一 call_tool 接口
                raw = f"(MCP client '{mcp_name}' has no call_tool method)"
            else:
                raw = "(unknown MCP client API)"
            return {"content": raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False, indent=2)}
        except Exception as e:
            return {"content": f"[Error] 调用工具 '{tool_name}' 失败: {e}"}

    # ---------- 对话执行（统一实现） ----------

    def _build_system_prompt(self) -> str:
        """生成用于 system role 的提示词（chat / chat_interactive 共用）。"""
        return f"""你是 {self.display_name} (v{self.version})。

角色描述: {self.description}

{self.system_prompt}

请以专业、清晰、步骤明确的方式执行任务。可以使用提供的工具。
"""

    def _execute_tool_call(self, tc: Dict[str, Any], turn: int) -> Dict[str, Any]:
        """解析并执行一次 LLM tool_call，返回标准化结果。

        返回: {"id": str, "name": str, "args": dict, "content": str}
        """
        tc_id = tc.get("id", f"call-{turn}")
        tc_func = tc.get("function", {}) or {}
        tc_name = tc_func.get("name", "")
        tc_args_raw = tc_func.get("arguments", "{}") or "{}"
        try:
            tc_args = json.loads(tc_args_raw) if isinstance(tc_args_raw, str) else tc_args_raw
        except Exception:
            tc_args = {}
        result = self._call_mcp_tool(tc_name, tc_args)
        content_str = result.get("content", "") if isinstance(result, dict) else str(result)
        return {"id": tc_id, "name": tc_name, "args": tc_args, "content": content_str}

    def _run_chat_core(
        self,
        messages: List[Dict[str, Any]],
        user_message: str,
        max_turns: int,
        assistant_prefix: str = "",
    ) -> List[Dict[str, Any]]:
        """核心对话循环（chat / chat_interactive 共用）。

        - 在 messages 尾部追加 user 消息
        - 循环：LLM 推理 -> (无 tool_calls => break, 有 tool_calls => 执行工具)
        - 工具调用结果追加到 messages，作为下一轮 LLM 上下文
        - 每轮 assistant 输出 & 工具调用在 stdout 打印并返回 history

        Args:
            messages: 已有消息列表（会被就地修改）
            user_message: 本轮用户消息
            max_turns: 最大工具调用轮次
            assistant_prefix: assistant 输出时的前缀标签

        Returns:
            history 列表：[{"turn": int, "type": "assistant"|"error", "content": str, "tool_calls": list}]
        """
        messages.append({"role": "user", "content": user_message})

        tools = self._build_tool_schema()
        history: List[Dict[str, Any]] = []

        for turn in range(max_turns):
            # 1. LLM 推理
            try:
                response = self._call_llm(messages, tools)
            except Exception as e:
                err_msg = f"[Error] LLM 调用失败: {e}"
                history.append({"turn": turn, "type": "error", "content": err_msg})
                messages.append({"role": "assistant", "content": err_msg})
                print(f"  {err_msg}")
                break

            msg = response.get("message", {}) or {}
            content = msg.get("content", "") or ""
            tool_calls = msg.get("tool_calls", []) or []

            # 2. 记录 & 打印 assistant 输出
            history.append({
                "turn": turn,
                "type": "assistant",
                "content": content,
                "tool_calls": tool_calls,
            })
            if content:
                label = assistant_prefix or f"[{self.display_name}]"
                print(f"\n  {label} {content}")

            if not tool_calls:
                break  # 对话自然结束

            # 3. 追加 assistant 消息（保留 tool_calls 供下一轮上下文）
            assistant_msg = {"role": "assistant", "content": content}
            assistant_msg["tool_calls"] = tool_calls
            messages.append(assistant_msg)

            # 4. 执行工具调用 & 追加 tool 消息
            for tc in tool_calls:
                executed = self._execute_tool_call(tc, turn)
                print(f"  [🛠️ tool_call] {executed['name']}({self._format_args(executed['args'])})")

                display = executed["content"]
                if isinstance(display, str) and len(display) > 500:
                    display = display[:500] + "..."
                print(f"  [📄 result] {display}")

                messages.append({
                    "role": "tool",
                    "tool_call_id": executed["id"],
                    "content": executed["content"],
                })

        return history

    def chat(self, user_message: str, max_turns: int = 15) -> List[Dict[str, Any]]:
        """执行一次对话（可能包含多轮工具调用）。

        Args:
            user_message: 用户消息
            max_turns: 最大工具调用轮次

        Returns:
            对话历史列表：[{"turn", "type": "assistant"|"error", "content", "tool_calls"}]
        """
        messages = [{"role": "system", "content": self._build_system_prompt()}]
        return self._run_chat_core(messages, user_message, max_turns)

    def chat_interactive(self, first_message: Optional[str] = None) -> None:
        """交互式对话。多轮之间自动保留 messages 上下文。"""
        print(f"\n{'='*60}")
        print(f" 🤖 {self.display_name} (v{self.version}) - 交互对话模式")
        print(f"    Agent ID: {self.agent_id}")
        print(f"    模型: {self.model_provider}/{self.model_id}")
        print(f"    WebBridge: {'已连接' if self._kimi_webbridge_enabled else '未连接'}")
        print(f"    MCP servers: {list(self._mcp_clients.keys())}")
        print(f"    输入 'quit'/'exit' 退出, 'clear'/'reset' 清空对话")
        print(f"{'='*60}\n")

        # 共享 messages 列表：首条 system prompt，后续每次用户输入追加 user + assistant + tool
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": self._build_system_prompt()},
        ]

        if first_message:
            self._run_chat_core(messages, first_message, max_turns=15)

        while True:
            try:
                user_input = input("\n你> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见！")
                break

            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit", "q"):
                print("再见！")
                break
            if user_input.lower() in ("clear", "reset"):
                messages = [{"role": "system", "content": self._build_system_prompt()}]
                print("(对话已清空)")
                continue

            self._run_chat_core(messages, user_input, max_turns=15)

    # ---------- LLM 调用 ----------

    def _build_headers(self) -> Dict[str, str]:
        """构建请求头，根据 provider 自动添加专用头"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        base_lower = (self.base_url or "").lower()
        provider_lower = (self.model_provider or "").lower()
        # OpenRouter 专用头 (https://openrouter.ai/docs/quick-start)
        if "openrouter" in provider_lower or "openrouter" in base_lower:
            headers["HTTP-Referer"] = "https://github.com/agent-hub/agent-compose"
            headers["X-Title"] = "Agent Hub"
        return headers

    def _call_llm(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """调用 LLM API"""
        body = {
            "model": self.model_id,
            "messages": messages,
            "temperature": 0.3,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        api_url = self.base_url.rstrip("/") + "/chat/completions"

        req_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            api_url,
            data=req_body,
            headers=self._build_headers(),
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                choice = data.get("choices", [{}])[0]
                msg = choice.get("message", {}) or {}
                return {"message": msg}
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"API 错误 {e.code}: {err_body[:500]}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"网络错误: {e}")

    # ---------- 工具 ----------

    @staticmethod
    def _format_args(args: Dict[str, Any]) -> str:
        parts = []
        for k, v in args.items():
            if isinstance(v, str) and len(v) > 80:
                v = v[:77] + "..."
            parts.append(f"{k}={json.dumps(v, ensure_ascii=False)}")
        return ", ".join(parts)

    # ---------- PipelineEngine ----------

    def initialize_pipeline_engine(self) -> PipelineEngine:
        """初始化 PipelineEngine，使用当前 tool_registry"""
        self.pipeline_engine = PipelineEngine(tool_registry=self.tool_registry)
        return self.pipeline_engine

    def execute_pipeline(
        self,
        pipeline_config: dict,
        initial_args: dict = None,
        timeout_ms: int = None,
    ) -> dict:
        """执行流水线配置

        Args:
            pipeline_config: 流水线配置字典
            initial_args: 初始参数
            timeout_ms: 超时时间（毫秒）

        Returns:
            执行结果字典: {"success": bool, "output": Any, "steps": list, "duration_ms": float}
        """
        import asyncio

        if self.pipeline_engine is None:
            self.initialize_pipeline_engine()

        context = ExecutionContext(
            agent_id=self.agent_id,
            initial_args=initial_args or {},
        )

        # 设置 trace 上下文
        set_trace_context(
            TraceContext(agent_id=self.agent_id)
        )

        # PipelineEngine.execute 是异步方法，需要运行事件循环
        result = asyncio.run(
            self.pipeline_engine.execute(
                pipeline_config=pipeline_config,
                context=context,
                timeout_ms=timeout_ms,
            )
        )
        return result

    def setup_observability(self, service_name: str = "agent-compose") -> Observability:
        """初始化可观测性模块

        Args:
            service_name: 服务名称

        Returns:
            Observability 实例
        """
        self.observability = Observability(service_name=service_name)
        return self.observability

    # ---------- 工厂方法 ----------

    @classmethod
    def from_market(
        cls,
        agent_id: str,
        market_client: Optional[MarketClient] = None,
        api_key: Optional[str] = None,
        webbridge_token: Optional[str] = None,
        model_provider: str = "kimi",
        model_id: str = "moonshot-v1-128k",
        base_url: Optional[str] = None,
        use_cache: bool = True,
        force_refresh: bool = False,
    ) -> "AgentRuntime":
        """从市场下载并创建 AgentRuntime"""
        market = market_client or MarketClient()
        agent_json, from_cache = market.fetch_agent_json(
            agent_id, use_cache=use_cache, force_refresh=force_refresh
        )
        runtime = cls(
            agent_id=agent_id,
            agent_json=agent_json,
            api_key=api_key,
            webbridge_token=webbridge_token,
            model_provider=model_provider,
            model_id=model_id,
            base_url=base_url,
        )
        return runtime

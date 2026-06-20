"""
MCPStdioClient - stdio 类型 MCP 客户端

通过子进程的 stdin/stdout 与 MCP Server 通信，
协议：JSON-RPC 2.0 over stdio（每行一条 JSON 消息）

权限问题排查（Windows）：
    1. 执行策略: Get-ExecutionPolicy -List  → 至少有一个为 RemoteSigned 或 Unrestricted
    2. 杀毒软件: 临时禁用或将 python.exe 加入白名单
    3. UAC: 以管理员身份运行，或在控制面板中调整用户权限
    4. 路径问题: 确保 command 在 PATH 中，或使用绝对路径
    5. 子进程阻塞: 使用 python -u 强制无缓冲输出

使用方法：
    client = MCPStdioClient(
        command="python",
        args=["-u", "-m", "agent_compose.mcp_servers.simple_mcp_server"],
        name="simple-calc"
    )
    client.connect()
    tools = client.list_tools()
    result = client.call_tool("calculator", {"expression": "2 + 3 * 4"})
    client.close()
"""

import json
import os
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional


class SubprocessPermissionError(RuntimeError):
    """子进程启动失败（通常是权限问题）"""
    pass


class MCPServerStartError(RuntimeError):
    """MCP Server 启动失败"""
    pass


def _diagnose_subprocess_error(exc: Exception, command: str, args: list) -> str:
    """生成人类可读的权限/启动错误诊断报告"""
    import sys, platform

    lines = [
        f"❌ 无法启动子进程: {exc}",
        "",
        f"   命令: {command} {' '.join(args)}",
        f"   Python: {sys.executable}",
        f"   平台: {platform.system()} {platform.release()}",
        f"   Python 版本: {sys.version}",
        "",
        "━━━ 常见原因与解决方案 ━━━",
    ]

    exc_msg = str(exc).lower()
    if "permission" in exc_msg or "access is denied" in exc_msg:
        lines.extend([
            "  🔒 权限被拒绝 (Permission Denied)",
            "     → 以管理员身份运行你的程序：",
            f"       右键 → 以管理员身份运行 → python {sys.argv[0]}",
            "     → 或在杀毒软件中将 python.exe / pythonw.exe 加入白名单",
            "     → 或在 Windows Defender 排除项中添加 Python 安装目录",
        ])
    elif "cannot find" in exc_msg or "no such file" in exc_msg:
        lines.extend([
            "  📁 命令不存在",
            f"     → 检查 '{command}' 是否在 PATH 中",
            "     → 或使用绝对路径，例如:",
            f"       MCPStdioClient(command=r'D:\\py3\\python.exe', ...)",
            f"       MCPStdioClient(command='D:\\py3\\python.exe', args=['-u', '-m', 'mymodule'])"
        ])
    elif "too many open files" in exc_msg or "errno 24" in exc_msg:
        lines.extend([
            "  📂 文件描述符耗尽",
            "     → 系统打开文件数超限，关闭不需要的程序后重试",
        ])
    elif "[WinError 5]" in str(exc):
        lines.extend([
            "  🔐 Windows Error 5: Access Denied",
            "     → 当前用户没有执行该进程的权限",
            "     → 方案1: 以管理员身份运行 Python",
            "     → 方案2: 检查杀毒软件是否拦截了 subprocess",
            "     → 方案3: 在 PowerShell 执行: Set-ExecutionPolicy RemoteSigned -Scope CurrentUser",
        ])
    else:
        lines.extend([
            "  ❓ 未知错误",
            "     → 确认命令可独立执行:",
            f"       {command} {' '.join(args[:3])}",
            "     → 确认 Python 路径正确:",
            f"       {sys.executable} --version",
        ])

    lines.extend([
        "",
        "━━━ 执行策略检查（PowerShell） ━━━",
        "   Get-ExecutionPolicy -List",
        "   如果当前进程是 Restricted，临时放行:",
        "   Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process",
    ])

    return "\n".join(lines)


class MCPStdioClient:
    """MCP stdio 客户端，支持 JSON-RPC 2.0 over stdio"""

    def __init__(
        self,
        command: str,
        args: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        name: str = "stdio-mcp",
        timeout: int = 30,
    ):
        self.command = command
        self.args = list(args) if args else []
        self.env = env or {}
        self.name = name
        self.timeout = timeout

        self._proc: Optional[subprocess.Popen] = None
        self._connected = False
        self._initialized = False
        self._tools_cache: Optional[List[Dict[str, Any]]] = None
        self._pending: Dict[str, tuple] = {}
        self._write_lock = threading.Lock()
        self._msg_counter = 0
        self._reader_thread: Optional[threading.Thread] = None

    # ---------- 连接管理 ----------

    def connect(self) -> bool:
        """启动子进程并完成 MCP 协议初始化"""
        try:
            self._start_subprocess()
            self._wait_for_initialization()
            return self._initialized
        except SubprocessPermissionError:
            raise  # 直接透传诊断信息
        except MCPServerStartError:
            raise
        except Exception as e:
            self._safe_kill()
            raise MCPServerStartError(
                f"[{self.name}] 启动失败: {e}\n"
                + _diagnose_subprocess_error(e, self.command, self.args)
            )

    def _start_subprocess(self) -> None:
        """启动子进程"""
        # 构建最终命令列表
        cmd = [self.command] + self.args

        # 确保 Python 使用无缓冲模式（-u 对 stdio 通信至关重要）
        py_exe = os.path.basename(self.command).lower()
        if py_exe in ("python", "python.exe", "python3", "python3.exe"):
            rest_args = list(self.args)
            if rest_args and rest_args[0] == "-u":
                pass  # 已有 -u
            elif rest_args and rest_args[0].startswith("-"):
                # 第一个参数是以 - 开头的 flag（如 -m, -c, -W 等）
                # 正确做法：在整个 [command, -x, ...] 前面单独插入 -u
                #   python -u -m module_name
                cmd = [self.command, "-u"] + rest_args
                return self._launch(cmd)
            else:
                # 普通参数，前面插入 -u
                cmd = [self.command, "-u"] + rest_args
                return self._launch(cmd)
            cmd = [self.command] + rest_args

        self._launch(cmd)

    def _launch(self, cmd: List[str]) -> None:
        """实际创建子进程的通用方法"""
        # 环境变量
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"  # 备用（部分情况比 -u 更可靠）
        env.update(self.env)

        # 创建进程（关键：不使用 shell=True，减少权限问题）
        startup_info = None
        if os.name == "nt":
            try:
                startup_info = subprocess.STARTUPINFO()
                startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startup_info.wShowWindow = subprocess.SW_HIDE
            except Exception:
                pass

        try:
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                bufsize=0,
                startupinfo=startup_info,
            )
        except PermissionError as e:
            raise SubprocessPermissionError(
                _diagnose_subprocess_error(e, self.command, self.args)
            )
        except FileNotFoundError as e:
            raise MCPServerStartError(
                _diagnose_subprocess_error(e, self.command, self.args)
            )
        except OSError as e:
            raise MCPServerStartError(
                _diagnose_subprocess_error(e, self.command, self.args)
            )

        # 确认进程还在运行
        poll = self._proc.poll()
        if poll is not None:
            stderr_out = b""
            try:
                _, se = self._proc.communicate(timeout=2)
                stderr_out = se
            except Exception:
                pass
            raise MCPServerStartError(
                f"[{self.name}] 子进程立即退出 (exit code {poll})\n"
                + f"   命令: {' '.join(cmd)}\n"
                + (f"   错误输出: {stderr_out.decode('utf-8', errors='replace')[:500]}" if stderr_out else "")
                + "\n   → 确认该命令可以直接执行"
            )

        # 启动 stdout 监听线程
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()
        self._connected = True

    def _wait_for_initialization(self) -> None:
        """发送 initialize 并等待 MCP 协议就绪"""
        init_id = f"init_{self._next_id()}"
        init_req = {
            "jsonrpc": "2.0",
            "id": init_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "clientInfo": {"name": "agent-compose", "version": "1.0.0"},
            },
        }
        result = self._send_and_wait(init_req, init_id)
        if result is None:
            # 超时：检查 stderr 是否有报错
            stderr_out = b""
            try:
                _, se = self._proc.communicate(timeout=1)
                stderr_out = se
            except Exception:
                pass
            raise MCPServerStartError(
                f"[{self.name}] MCP 协议初始化超时 (2s)\n"
                + f"   stderr: {stderr_out.decode('utf-8', errors='replace')[:300]}\n"
                + "   → 确认 MCP Server 支持 2024-11-05 协议版本"
            )

        # 发送 initialized 通知（MCP 协议要求）
        self._send_nowait({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self._initialized = True
        print(f"[MCPStdioClient:{self.name}] MCP 协议初始化完成 (protocol 2024-11-05)")

    def close(self) -> None:
        if self._proc:
            try:
                self._send_nowait({"jsonrpc": "2.0", "method": "notifications/exit"})
            except Exception:
                pass
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.wait(timeout=3)
            except Exception:
                self._safe_kill()
        self._connected = False
        self._initialized = False
        print(f"[MCPStdioClient:{self.name}] 已关闭")

    def _safe_kill(self) -> None:
        try:
            if self._proc and self._proc.poll() is None:
                self._proc.kill()
        except Exception:
            pass

    # ---------- 读写循环 ----------

    def _read_loop(self) -> None:
        try:
            assert self._proc is not None
            while self._proc.poll() is None:
                line = self._proc.stdout.readline()
                if not line:
                    break
                try:
                    text = line.decode("utf-8", errors="ignore").strip()
                    if not text:
                        continue
                    msg = json.loads(text)
                    self._handle_message(msg)
                except (json.JSONDecodeError, TypeError):
                    pass
        except Exception:
            pass

    def _handle_message(self, msg: Dict[str, Any]) -> None:
        msg_id = msg.get("id")
        if msg_id and msg_id in self._pending:
            event, result_holder = self._pending[msg_id]
            result_holder["result"] = msg
            event.set()

    def _send_nowait(self, msg: Dict[str, Any]) -> None:
        if not self._proc or not self._proc.stdin:
            return
        line = json.dumps(msg, ensure_ascii=False) + "\n"
        with self._write_lock:
            try:
                self._proc.stdin.write(line.encode("utf-8"))
                self._proc.stdin.flush()
            except BrokenPipeError:
                self._connected = False

    def _send_and_wait(self, msg: Dict[str, Any], request_id: str) -> Optional[Dict[str, Any]]:
        event = threading.Event()
        result_holder: Dict[str, Any] = {}
        self._pending[request_id] = (event, result_holder)
        try:
            self._send_nowait(msg)
            if event.wait(timeout=self.timeout):
                result = result_holder.get("result")
                if isinstance(result, dict) and "id" in result:
                    if "error" in result:
                        return {"_error": result["error"]}
                    if "result" in result:
                        return result["result"]
                    return result
                return result
            return None
        finally:
            self._pending.pop(request_id, None)

    def _next_id(self) -> int:
        self._msg_counter += 1
        return self._msg_counter

    # ---------- MCP 工具 API ----------

    def list_tools(self) -> List[Dict[str, Any]]:
        if self._tools_cache:
            return self._tools_cache
        if not self._connected or not self._initialized:
            print(f"[MCPStdioClient:{self.name}] 未初始化")
            return []

        req_id = f"tools_{self._next_id()}"
        msg = {"jsonrpc": "2.0", "id": req_id, "method": "tools/list"}
        result = self._send_and_wait(msg, req_id)

        if result is None:
            print(f"[MCPStdioClient:{self.name}] list_tools 超时")
            return []

        tools = []
        if isinstance(result, dict):
            tools = result.get("tools", [])
        elif isinstance(result, list):
            tools = result

        self._tools_cache = tools
        print(f"[MCPStdioClient:{self.name}] 发现 {len(tools)} 个工具:")
        for t in tools:
            print(f"    - {t.get('name', '?')}: {str(t.get('description', ''))[:60]}")
        return tools

    def call_tool(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self._connected or not self._initialized:
            return {"content": f"[Error] MCP client '{self.name}' 未初始化"}

        # 工具名后处理：去掉 <|channel|> 等残留标记
        clean_name = tool_name.split("<")[0].strip()

        req_id = f"call_{self._next_id()}"
        msg = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {
                "name": clean_name,
                "arguments": arguments or {},
            },
        }
        result = self._send_and_wait(msg, req_id)

        if result is None:
            return {"content": f"[Error] 调用 '{clean_name}' 超时或失败"}

        if isinstance(result, dict) and "error" in result:
            return {"content": f"[Error] {result['error']}"}

        inner = result if isinstance(result, dict) else {}
        content = inner.get("content", [])
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parts.append(str(item.get("text", "")))
                    elif item.get("type") == "image":
                        parts.append(f"[IMAGE data={str(item.get('data', ''))[:50]}]")
                    else:
                        parts.append(str(item))
                else:
                    parts.append(str(item))
            return {"content": "\n".join(parts) if parts else str(inner)}
        return {"content": str(content) if content else str(inner)}

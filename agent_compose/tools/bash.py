"""
bash 工具 - Shell 命令执行

支持:
- 命令执行（bash -c / cmd /c）
- 超时控制
- 危险命令检测
- 工作目录和环境变量
"""

import asyncio
import os
import platform
import re
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional

# 危险命令模式（与 agent-deploy 保持一致）
DANGEROUS_COMMAND_PATTERNS: List[re.Pattern] = [
    re.compile(r"rm\s+-rf\s+/\s*($|\\s)"),
    re.compile(r"rm\s+-rf\s+/\*"),
    re.compile(r"chmod\s+-R\s+777\s+/"),
    re.compile(r">\s*/dev/sda"),
    re.compile(r"dd\s+if="),
    re.compile(r"mkfs\."),
    re.compile(r":\(\)\s*\{\s*:\|:\&"),  # fork bomb
    re.compile(r"curl\s+.*\|\s*sh"),  # pipe curl to shell
    re.compile(r"wget\s+.*\|\s*sh"),  # pipe wget to shell
]


def _is_dangerous(command: str) -> bool:
    """检查命令是否包含危险模式"""
    for pattern in DANGEROUS_COMMAND_PATTERNS:
        if pattern.search(command):
            return True
    return False


def _get_shell() -> tuple:
    """获取当前平台的 shell 命令"""
    system = platform.system()
    if system == "Windows":
        # 优先使用 PowerShell，回退到 cmd
        if shutil.which("powershell"):
            return (["powershell", "-Command"], "powershell")
        return (["cmd", "/c"], "cmd")
    # Unix-like
    if shutil.which("bash"):
        return (["bash", "-c"], "bash")
    if shutil.which("sh"):
        return (["sh", "-c"], "sh")
    raise RuntimeError("No shell found (bash/sh on Unix, cmd/powershell on Windows)")


async def bash_tool(args: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    执行 shell 命令

    Args:
        args: {
            "command": str,           # 必需: 要执行的命令
            "timeout": int,           # 可选: 超时时间(毫秒), 默认 60000
            "cwd": str,               # 可选: 工作目录
            "env": Dict[str, str],    # 可选: 额外环境变量
        }
        context: ExecutionContext (用于获取 cwd, env)

    Returns:
        {
            "stdout": str,
            "stderr": str,
            "exit_code": int,
            "duration_ms": float,
        }
    """
    command = args.get("command")
    if not command or not isinstance(command, str):
        raise ValueError("bash: 'command' parameter is required and must be a string")

    command = command.strip()
    if not command:
        raise ValueError("bash: 'command' parameter cannot be empty")

    # 危险命令检测
    if _is_dangerous(command):
        raise PermissionError("bash: Command blocked by security policy: dangerous pattern detected")

    # 超时设置
    timeout_ms = args.get("timeout", 60000)
    if timeout_ms <= 0:
        timeout_ms = 60000
    timeout_sec = timeout_ms / 1000.0

    # 工作目录
    cwd = args.get("cwd") or (context.cwd if hasattr(context, "cwd") else None)
    if cwd:
        cwd = os.path.abspath(cwd)
        if not os.path.isdir(cwd):
            raise FileNotFoundError(f"bash: Working directory does not exist: {cwd}")

    # 环境变量
    env = dict(os.environ)
    if hasattr(context, "env") and context.env:
        env.update(context.env)
    extra_env = args.get("env")
    if extra_env:
        env.update(extra_env)

    # 获取 shell
    shell_cmd, shell_name = _get_shell()

    start_time = time.time()

    try:
        proc = await asyncio.create_subprocess_exec(
            *shell_cmd,
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_sec
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"bash: Command timed out after {timeout_ms}ms")

        duration_ms = (time.time() - start_time) * 1000

        stdout = stdout_bytes.decode("utf-8", errors="replace").strip() if stdout_bytes else ""
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip() if stderr_bytes else ""
        exit_code = proc.returncode if proc.returncode is not None else -1

        return {
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "duration_ms": round(duration_ms, 2),
        }

    except (TimeoutError, ValueError, PermissionError, FileNotFoundError):
        raise
    except Exception as e:
        raise RuntimeError(f"bash: Failed to execute command: {e}")

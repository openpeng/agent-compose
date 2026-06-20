"""
simple_mcp_server - 示例 stdio MCP 服务器

使用官方 mcp SDK 实现，暴露以下工具：
  1. calculator(expression)  — 计算数学表达式
  2. echo(message)           — 回显消息
  3. now()                   — 返回当前时间
  4. file_read(path)         — 读取本地文件内容
  5. list_dir(path)          — 列出目录内容

运行方式:
    python -m agent_compose.mcp_servers.simple_mcp_server
"""
import json
import os
import sys
import math
import random
from datetime import datetime
from typing import Any, Dict, List

from mcp.server.fastmcp import FastMCP


def _safe_eval(expression: str) -> float:
    """安全地计算数学表达式"""
    allowed = set(
        "0123456789+-*/().%^eE ,\n\t"
        + "".join(dir(math))
        + "".join(["abs", "int", "float", "min", "max", "round", "pow", "sqrt", "pi", "e"])
    )
    expr = expression.strip()
    if not expr:
        raise ValueError("表达式为空")

    # 安全白名单检查：逐个字符验证
    for ch in expr:
        if ch.isalpha():
            continue  # 允许 math 函数调用
        if ch not in "0123456789+-*/().%^eE ,":
            raise ValueError(f"非法字符: '{ch}'")

    safe_namespace = {
        "abs": abs,
        "int": int,
        "float": float,
        "min": min,
        "max": max,
        "round": round,
        "pow": pow,
        "sqrt": math.sqrt,
        "pi": math.pi,
        "e": math.e,
        "sin": math.sin,
        "cos": math.cos,
        "tan": math.tan,
        "log": math.log,
        "log10": math.log10,
        "log2": math.log2,
        "exp": math.exp,
        "ceil": math.ceil,
        "floor": math.floor,
        "factorial": math.factorial,
    }
    return eval(expr, {"__builtins__": {}}, safe_namespace)


def main() -> int:
    server = FastMCP("AgentCompose Demo Server")

    @server.tool("calculator", description="计算数学表达式。支持 + - * / () 和基本数学函数 sin/cos/sqrt/log/pi/e 等。")
    def calculator(expression: str) -> str:
        """计算数学表达式"""
        try:
            value = _safe_eval(expression)
            return f"{value}"
        except Exception as e:
            return f"[Error] {e}"

    @server.tool("echo", description="原样回显输入消息，用于调试和测试。")
    def echo(message: str) -> str:
        return f"echo: {message}"

    @server.tool("now", description="获取当前的日期和时间（ISO 格式）。")
    def now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @server.tool("file_read", description="读取一个本地文件的内容。path 为文件的绝对或相对路径。")
    def file_read(path: str) -> str:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except Exception as e:
            return f"[Error] 读取文件失败: {e}"

    @server.tool("list_dir", description="列出一个目录下的文件和子目录。path 为目录的绝对或相对路径。")
    def list_dir(path: str) -> str:
        try:
            items = sorted(os.listdir(path))
            return "\n".join(items)
        except Exception as e:
            return f"[Error] 读取目录失败: {e}"

    @server.tool("random_between", description="返回指定范围内 [min, max] 的随机整数。")
    def random_between(min: int, max: int) -> str:
        return f"{random.randint(min, max)}"

    # 启动 stdio 服务器
    server.run(transport="stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())

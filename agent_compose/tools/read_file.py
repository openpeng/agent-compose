"""
read_file 工具 - 文件读取

支持:
- 相对/绝对路径读取
- 编码指定
- 大小限制
- 路径安全检查
"""

import os
from typing import Any, Dict, List, Optional

# 默认阻止的路径模式
DEFAULT_BLOCKED_PATHS: List[str] = [
    "/etc/shadow",
    "/etc/passwd",
    "/etc/hosts",
    "/proc",
    "/sys",
    "/dev",
    "C:\\Windows\\System32",
    "C:\\Windows\\SysWOW64",
    "etc",  # 跨平台: 阻止 etc 目录
    "System32",  # 跨平台: 阻止 System32
]

DEFAULT_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


def _is_path_blocked(abs_path: str, blocked_paths: Optional[List[str]] = None) -> bool:
    """检查路径是否在阻止列表中"""
    blocked = blocked_paths or DEFAULT_BLOCKED_PATHS
    abs_path = os.path.normpath(abs_path).lower()
    for bp in blocked:
        bp_norm = os.path.normpath(bp).lower()
        if abs_path.startswith(bp_norm):
            return True
        # 跨平台: 也检查路径组件匹配
        abs_parts = abs_path.split(os.sep)
        bp_parts = bp_norm.split(os.sep)
        if len(bp_parts) == 1 and bp_parts[0] in abs_parts:
            return True
        # 检查 Unix 风格路径在 Windows 上
        if os.sep == "\\":
            unix_bp = bp_norm.replace("\\", "/")
            unix_abs = abs_path.replace("\\", "/")
            if unix_abs.startswith(unix_bp):
                return True
            unix_parts = unix_abs.split("/")
            if len(bp_parts) == 1 and bp_parts[0] in unix_parts:
                return True
    return False


def _is_path_allowed(abs_path: str, allowed_paths: Optional[List[str]] = None) -> bool:
    """检查路径是否在允许列表中（白名单模式）"""
    if not allowed_paths:
        return True
    abs_path = os.path.normpath(abs_path).lower()
    for ap in allowed_paths:
        ap_norm = os.path.normpath(ap).lower()
        if abs_path.startswith(ap_norm):
            return True
    return False


def _resolve_path(path: str, cwd: Optional[str] = None) -> str:
    """解析为绝对路径"""
    if os.path.isabs(path):
        return os.path.normpath(path)
    base = cwd or os.getcwd()
    return os.path.normpath(os.path.join(base, path))


async def read_file_tool(args: Dict[str, Any], context: Any) -> str:
    """
    读取文件内容

    Args:
        args: {
            "path": str,          # 必需: 文件路径
            "encoding": str,      # 可选: 编码, 默认 "utf-8"
            "max_size": int,      # 可选: 最大读取字节数, 默认 10MB
        }
        context: ExecutionContext (用于获取 cwd)

    Returns:
        文件文本内容 (str)
    """
    path = args.get("path")
    if not path or not isinstance(path, str):
        raise ValueError("read_file: 'path' parameter is required and must be a string")

    # 解析路径
    cwd = args.get("cwd") or (context.cwd if hasattr(context, "cwd") else None)
    abs_path = _resolve_path(path, cwd)

    # 安全检查 (在文件存在性检查之前)
    if _is_path_blocked(abs_path):
        raise PermissionError(f"read_file: Access to path is blocked by security policy: {path}")

    # 文件存在性检查
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"read_file: File not found: {path}")

    if not os.path.isfile(abs_path):
        raise IsADirectoryError(f"read_file: Path is not a file: {path}")

    # 大小检查
    max_size = args.get("max_size", DEFAULT_MAX_FILE_SIZE)
    stats = os.stat(abs_path)
    if stats.st_size > max_size:
        raise ValueError(
            f"read_file: File size ({stats.st_size} bytes) exceeds maximum allowed ({max_size} bytes)"
        )

    # 读取文件
    encoding = args.get("encoding", "utf-8")
    try:
        with open(abs_path, "r", encoding=encoding, errors="replace") as f:
            content = f.read()
        return content
    except PermissionError as e:
        raise PermissionError(f"read_file: Permission denied: {path}") from e
    except Exception as e:
        raise RuntimeError(f"read_file: Failed to read file '{path}': {e}") from e

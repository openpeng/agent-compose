"""
write_file 工具 - 文件写入

支持:
- 覆盖/追加模式
- 自动创建父目录
- 路径安全检查
- 编码指定
"""

import os
from typing import Any, Dict, List, Optional

# 复用 read_file 的安全检查
from .read_file import _is_path_blocked, _resolve_path

DEFAULT_MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB


async def write_file_tool(args: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    写入文件内容

    Args:
        args: {
            "path": str,              # 必需: 文件路径
            "content": str,           # 必需: 写入内容
            "mode": str,              # 可选: "overwrite" | "append", 默认 "overwrite"
            "create_dirs": bool,      # 可选: 是否自动创建父目录, 默认 True
            "encoding": str,          # 可选: 编码, 默认 "utf-8"
        }
        context: ExecutionContext (用于获取 cwd)

    Returns:
        {
            "path": str,              # 实际写入的绝对路径
            "bytes_written": int,     # 写入后文件总大小
        }
    """
    path = args.get("path")
    if not path or not isinstance(path, str):
        raise ValueError("write_file: 'path' parameter is required and must be a string")

    content = args.get("content")
    if content is None:
        raise ValueError("write_file: 'content' parameter is required")

    # 解析路径
    cwd = args.get("cwd") or (context.cwd if hasattr(context, "cwd") else None)
    abs_path = _resolve_path(path, cwd)

    # 安全检查
    if _is_path_blocked(abs_path):
        raise PermissionError(f"write_file: Access to path is blocked by security policy: {path}")

    # 自动创建父目录
    create_dirs = args.get("create_dirs", True)
    if create_dirs:
        parent_dir = os.path.dirname(abs_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

    # 写入模式
    mode = args.get("mode", "overwrite")
    encoding = args.get("encoding", "utf-8")

    try:
        if mode == "append":
            with open(abs_path, "a", encoding=encoding, errors="replace") as f:
                f.write(content)
        else:
            with open(abs_path, "w", encoding=encoding, errors="replace") as f:
                f.write(content)

        # 获取写入后大小
        bytes_written = os.path.getsize(abs_path)

        return {
            "path": abs_path,
            "bytes_written": bytes_written,
        }

    except PermissionError as e:
        raise PermissionError(f"write_file: Permission denied: {path}") from e
    except OSError as e:
        if e.errno == 28:  # ENOSPC
            raise OSError(f"write_file: No space left on device: {path}") from e
        raise RuntimeError(f"write_file: Failed to write file '{path}': {e}") from e
    except Exception as e:
        raise RuntimeError(f"write_file: Failed to write file '{path}': {e}") from e

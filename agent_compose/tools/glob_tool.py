"""
glob 工具 - 文件模式匹配

支持:
- glob 模式匹配
- 忽略模式
- 绝对/相对路径返回
"""

import fnmatch
import os
from typing import Any, Dict, List, Optional


def _glob_recursive(pattern: str, cwd: str, ignore_patterns: List[str]) -> List[str]:
    """递归 glob 实现"""
    results = []

    # 处理绝对路径模式
    if os.path.isabs(pattern):
        base_dir = "/"
        rel_pattern = pattern.lstrip("/").lstrip("\\")
    else:
        base_dir = cwd
        rel_pattern = pattern

    # 分割目录和文件名模式
    parts = rel_pattern.replace("\\", "/").split("/")

    def walk(current_dir: str, remaining_parts: List[str]) -> List[str]:
        if not remaining_parts:
            return []

        part = remaining_parts[0]
        rest = remaining_parts[1:]

        if not os.path.isdir(current_dir):
            return []

        matches = []
        try:
            entries = os.listdir(current_dir)
        except (PermissionError, OSError):
            return []

        for entry in entries:
            # 跳过隐藏目录和常见忽略项
            if entry.startswith(".") and part != ".*":
                if entry in (".git", ".svn", ".hg", ".trae", ".trae-cn"):
                    continue

            full_path = os.path.join(current_dir, entry)

            # 检查忽略模式
            rel_to_cwd = os.path.relpath(full_path, cwd).replace("\\", "/")
            ignored = False
            for ignore in ignore_patterns:
                if fnmatch.fnmatch(rel_to_cwd, ignore) or fnmatch.fnmatch(entry, ignore):
                    ignored = True
                    break
            if ignored:
                continue

            if fnmatch.fnmatch(entry, part) or (part == "**" and os.path.isdir(full_path)):
                if not rest:
                    if os.path.isfile(full_path):
                        matches.append(full_path)
                else:
                    if part == "**":
                        # ** 可以匹配零层或多层
                        matches.extend(walk(full_path, rest))
                        matches.extend(walk(full_path, remaining_parts))
                    elif os.path.isdir(full_path):
                        matches.extend(walk(full_path, rest))

        return matches

    return walk(base_dir, parts)


async def glob_tool(args: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    文件模式匹配

    Args:
        args: {
            "pattern": str,           # 必需: glob 匹配模式
            "cwd": str,               # 可选: 工作目录
            "ignore": List[str],      # 可选: 忽略模式列表
            "absolute": bool,         # 可选: 返回绝对路径, 默认 True
        }
        context: ExecutionContext (用于获取 cwd)

    Returns:
        {
            "files": List[str],       # 匹配的文件路径列表
            "pattern": str,           # 原始匹配模式
            "count": int,             # 匹配文件数量
        }
    """
    pattern = args.get("pattern")
    if not pattern or not isinstance(pattern, str):
        raise ValueError("glob: 'pattern' parameter is required and must be a string")

    # 工作目录
    cwd = args.get("cwd") or (context.cwd if hasattr(context, "cwd") else os.getcwd())
    cwd = os.path.abspath(cwd)

    # 忽略模式
    ignore = args.get("ignore", [])
    default_ignore = ["node_modules/**", ".git/**", "__pycache__/**", "*.pyc", ".trae/**"]
    ignore_patterns = default_ignore + (ignore if isinstance(ignore, list) else [])

    # 执行匹配
    files = _glob_recursive(pattern, cwd, ignore_patterns)

    # 去重并排序
    files = sorted(set(files))

    # 路径格式
    absolute = args.get("absolute", True)
    if not absolute:
        files = [os.path.relpath(f, cwd) for f in files]

    return {
        "files": files,
        "pattern": pattern,
        "count": len(files),
    }

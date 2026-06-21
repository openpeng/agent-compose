"""
Log Rotation - 日志轮转模块

提供:
- 基于大小的日志轮转
- 基于时间的日志轮转（按天）
- JSON 结构化日志支持
- 旧日志 gzip 压缩
- 与 observability.StructuredLogger 集成

设计原则:
- 零外部依赖 (纯 Python, 使用标准库 gzip)
- 线程安全
- 与现有 StructuredLogger 兼容
"""

import gzip
import json
import os
import re
import shutil
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


# ============ 基础轮转处理器 ============


class RotatingFileHandler:
    """日志轮转文件处理器

    支持基于大小和基于时间的轮转策略，可自动压缩旧日志。

    Args:
        filepath: 日志文件路径
        max_bytes: 单个日志文件最大字节数（0 表示不限制）
        backup_count: 保留的备份文件数量
        rotate_daily: 是否按天轮转
        compress: 是否压缩旧日志
        encoding: 文件编码
    """

    def __init__(
        self,
        filepath: str,
        max_bytes: int = 10 * 1024 * 1024,  # 10MB
        backup_count: int = 5,
        rotate_daily: bool = True,
        compress: bool = True,
        encoding: str = "utf-8",
    ):
        self.filepath = Path(filepath)
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.rotate_daily = rotate_daily
        self.compress = compress
        self.encoding = encoding

        self._lock = threading.Lock()
        self._current_date = datetime.now().date()
        self._file: Optional[Any] = None
        self._current_size = 0

        # 确保目录存在
        self.filepath.parent.mkdir(parents=True, exist_ok=True)

        # 初始化文件
        self._open_file()

    def _open_file(self) -> None:
        """打开当前日志文件"""
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass

        mode = "a" if self.filepath.exists() else "w"
        self._file = open(self.filepath, mode, encoding=self.encoding)
        self._current_size = self.filepath.stat().st_size if self.filepath.exists() else 0
        self._current_date = datetime.now().date()

    def _should_rotate(self, message_bytes: int) -> bool:
        """判断是否需要轮转"""
        if self.rotate_daily and datetime.now().date() != self._current_date:
            return True
        if self.max_bytes > 0 and (self._current_size + message_bytes) > self.max_bytes:
            return True
        return False

    def _rotate(self) -> None:
        """执行日志轮转"""
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None

        if not self.filepath.exists():
            return

        # 生成备份文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = self.filepath.stem
        suffix = self.filepath.suffix
        backup_dir = self.filepath.parent

        # 移动现有备份
        self._shift_backups(base_name, suffix, backup_dir)

        # 将当前日志移到 .1
        backup_path = backup_dir / f"{base_name}.{timestamp}{suffix}"
        try:
            shutil.move(str(self.filepath), str(backup_path))
        except Exception:
            pass

        # 压缩旧日志
        if self.compress:
            self._compress_old_logs(base_name, suffix, backup_dir)

        # 重新打开文件
        self._open_file()

    def _shift_backups(self, base_name: str, suffix: str, backup_dir: Path) -> None:
        """移动现有备份文件（保留 backup_count 个）"""
        pattern = re.compile(re.escape(base_name) + r"\.(\d{8}_\d{6})" + re.escape(suffix) + r"(\.gz)?$")
        backups = []

        for f in backup_dir.iterdir():
            match = pattern.match(f.name)
            if match:
                backups.append((match.group(1), f))

        # 按时间排序，删除旧的
        backups.sort(key=lambda x: x[0], reverse=True)
        for _, old_file in backups[self.backup_count - 1:]:
            try:
                old_file.unlink()
            except Exception:
                pass

    def _compress_old_logs(self, base_name: str, suffix: str, backup_dir: Path) -> None:
        """压缩未压缩的旧日志文件"""
        pattern = re.compile(re.escape(base_name) + r"\.(\d{8}_\d{6})" + re.escape(suffix) + r"$")

        for f in backup_dir.iterdir():
            if pattern.match(f.name):
                gz_path = f.with_suffix(f.suffix + ".gz")
                try:
                    with open(f, "rb") as src, gzip.open(gz_path, "wb") as dst:
                        dst.writelines(src)
                    f.unlink()
                except Exception:
                    pass

    def write(self, message: str) -> None:
        """写入日志消息"""
        encoded = message.encode(self.encoding) if isinstance(message, str) else message
        message_bytes = len(encoded)

        with self._lock:
            if self._should_rotate(message_bytes):
                self._rotate()

            if self._file is None:
                self._open_file()

            self._file.write(message + "\n")
            self._file.flush()
            self._current_size += message_bytes + 1  # +1 for newline

    def close(self) -> None:
        """关闭日志文件"""
        with self._lock:
            if self._file is not None:
                try:
                    self._file.close()
                except Exception:
                    pass
                self._file = None

    def get_stats(self) -> Dict[str, Any]:
        """获取处理器统计信息"""
        return {
            "filepath": str(self.filepath),
            "max_bytes": self.max_bytes,
            "backup_count": self.backup_count,
            "rotate_daily": self.rotate_daily,
            "compress": self.compress,
            "current_size": self._current_size,
            "current_date": str(self._current_date),
        }

    def list_backups(self) -> List[Dict[str, Any]]:
        """列出所有备份文件"""
        base_name = self.filepath.stem
        suffix = self.filepath.suffix
        backup_dir = self.filepath.parent
        pattern = re.compile(re.escape(base_name) + r"\.(\d{8}_\d{6})" + re.escape(suffix) + r"(\.gz)?$")

        backups = []
        for f in backup_dir.iterdir():
            match = pattern.match(f.name)
            if match:
                stat = f.stat()
                backups.append({
                    "filename": f.name,
                    "path": str(f),
                    "size": stat.st_size,
                    "compressed": f.name.endswith(".gz"),
                    "mtime": stat.st_mtime,
                })

        backups.sort(key=lambda x: x["mtime"], reverse=True)
        return backups

    def cleanup_old_logs(self, max_age_days: int = 30) -> int:
        """清理超过指定天数的旧日志

        Returns:
            删除的文件数量
        """
        cutoff = time.time() - (max_age_days * 86400)
        removed = 0
        base_name = self.filepath.stem
        suffix = self.filepath.suffix
        backup_dir = self.filepath.parent
        pattern = re.compile(re.escape(base_name) + r"\.(\d{8}_\d{6})" + re.escape(suffix) + r"(\.gz)?$")

        for f in backup_dir.iterdir():
            if pattern.match(f.name):
                try:
                    if f.stat().st_mtime < cutoff:
                        f.unlink()
                        removed += 1
                except Exception:
                    pass

        return removed


# ============ JSON 结构化日志轮转处理器 ============


class JSONRotatingHandler(RotatingFileHandler):
    """JSON 结构化日志轮转处理器

    继承 RotatingFileHandler，确保每行输出为合法 JSON。
    """

    def write(self, message: str) -> None:
        """写入 JSON 日志行，自动验证 JSON 格式"""
        # 确保消息是单行 JSON
        stripped = message.strip()
        if stripped:
            try:
                json.loads(stripped)
            except json.JSONDecodeError:
                # 如果不是合法 JSON，包装为 JSON
                stripped = json.dumps({"message": stripped, "timestamp": time.time()}, ensure_ascii=False)
            super().write(stripped)


# ============ 与 observability 模块集成 ============


def create_rotating_logger(
    name: str = "agent-compose",
    log_dir: str = "./logs",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    rotate_daily: bool = True,
    compress: bool = True,
    level: str = "INFO",
) -> "StructuredLogger":
    """创建带轮转的 StructuredLogger

    Args:
        name: 日志名称
        log_dir: 日志目录
        max_bytes: 单个文件最大字节数
        backup_count: 备份数量
        rotate_daily: 是否按天轮转
        compress: 是否压缩旧日志
        level: 日志级别

    Returns:
        StructuredLogger 实例
    """
    from .observability import StructuredLogger, LogLevel

    log_path = Path(log_dir) / f"{name}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    handler = JSONRotatingHandler(
        filepath=str(log_path),
        max_bytes=max_bytes,
        backup_count=backup_count,
        rotate_daily=rotate_daily,
        compress=compress,
    )

    def output_fn(line: str) -> None:
        handler.write(line)

    try:
        log_level = LogLevel[level.upper()]
    except KeyError:
        log_level = LogLevel.INFO

    return StructuredLogger(name=name, level=log_level, output=output_fn)


# ============ 便捷函数 ============


def rotate_log_file(filepath: str, compress: bool = True) -> Optional[str]:
    """手动轮转单个日志文件

    Args:
        filepath: 日志文件路径
        compress: 是否压缩

    Returns:
        备份文件路径，失败返回 None
    """
    path = Path(filepath)
    if not path.exists():
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = path.parent / f"{path.stem}.{timestamp}{path.suffix}"

    try:
        shutil.move(str(path), str(backup_path))
        if compress:
            gz_path = backup_path.with_suffix(backup_path.suffix + ".gz")
            with open(backup_path, "rb") as src, gzip.open(gz_path, "wb") as dst:
                dst.writelines(src)
            backup_path.unlink()
            return str(gz_path)
        return str(backup_path)
    except Exception:
        return None


def get_log_files(log_dir: str, pattern: str = "*.jsonl*") -> List[Dict[str, Any]]:
    """获取日志目录中的日志文件列表

    Args:
        log_dir: 日志目录
        pattern: 文件匹配模式

    Returns:
        日志文件信息列表
    """
    directory = Path(log_dir)
    if not directory.exists():
        return []

    files = []
    for f in directory.glob(pattern):
        stat = f.stat()
        files.append({
            "name": f.name,
            "path": str(f),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "compressed": f.name.endswith(".gz"),
        })

    files.sort(key=lambda x: x["mtime"], reverse=True)
    return files

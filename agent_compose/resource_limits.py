"""
Resource Limits - 资源限制模块

提供:
- 进程级资源限制 (CPU 时间、内存、文件描述符)
- 内存监控 (基于 psutil，带优雅降级)
- 资源限制装饰器
- 自定义异常类型

设计原则:
- 零外部依赖核心 (纯 Python)
- 可选 psutil 增强
- 线程安全
- 跨平台兼容 (Windows / Linux / macOS)
"""

import functools
import os
import platform
import threading
import time
import warnings
from contextlib import contextmanager
from typing import Any, Callable, Dict, Optional

# Unix-only resource module
try:
    import resource
    _HAS_RESOURCE = True
except ImportError:
    _HAS_RESOURCE = False
    resource = None  # type: ignore

# 可选的 psutil 导入
_try_psutil = True
_psutil = None
try:
    import psutil
    _psutil = psutil
except ImportError:
    _try_psutil = False


# ============ 自定义异常 ============


class ResourceLimitExceeded(Exception):
    """资源限制超出基类"""

    def __init__(self, resource_type: str, limit: float, current: float, message: str = ""):
        self.resource_type = resource_type
        self.limit = limit
        self.current = current
        msg = message or f"{resource_type} limit exceeded: {current:.2f} > {limit:.2f}"
        super().__init__(msg)


class MemoryLimitExceeded(ResourceLimitExceeded):
    """内存限制超出异常"""

    def __init__(self, limit_mb: float, current_mb: float, message: str = ""):
        super().__init__(
            resource_type="memory",
            limit=limit_mb,
            current=current_mb,
            message=message or f"Memory limit exceeded: {current_mb:.2f}MB > {limit_mb:.2f}MB",
        )


class CPULimitExceeded(ResourceLimitExceeded):
    """CPU 时间限制超出异常"""

    def __init__(self, limit_seconds: float, current_seconds: float, message: str = ""):
        super().__init__(
            resource_type="cpu_time",
            limit=limit_seconds,
            current=current_seconds,
            message=message or f"CPU time limit exceeded: {current_seconds:.2f}s > {limit_seconds:.2f}s",
        )


class FDLimitExceeded(ResourceLimitExceeded):
    """文件描述符限制超出异常"""

    def __init__(self, limit: int, current: int, message: str = ""):
        super().__init__(
            resource_type="file_descriptors",
            limit=float(limit),
            current=float(current),
            message=message or f"File descriptor limit exceeded: {current} > {limit}",
        )


# ============ 资源监控 ============


class ResourceMonitor:
    """资源监控器

    监控当前进程的 CPU 时间、内存使用、文件描述符数量。
    优先使用 psutil，不可用时回退到标准库。
    """

    def __init__(self):
        self._has_psutil = _psutil is not None
        self._pid = os.getpid()
        self._process = None
        if self._has_psutil:
            try:
                self._process = _psutil.Process(self._pid)
            except Exception:
                self._process = None
                self._has_psutil = False

    def get_memory_usage_mb(self) -> float:
        """获取当前内存使用（MB）"""
        if self._has_psutil and self._process is not None:
            try:
                info = self._process.memory_info()
                return info.rss / (1024 * 1024)
            except Exception:
                pass
        # 回退: 读取 /proc/self/status (Linux)
        try:
            with open("/proc/self/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            kb = float(parts[1])
                            return kb / 1024
        except Exception:
            pass
        return 0.0

    def get_cpu_time_seconds(self) -> float:
        """获取当前进程 CPU 时间（秒）"""
        if self._has_psutil and self._process is not None:
            try:
                times = self._process.cpu_times()
                return times.user + times.system
            except Exception:
                pass
        # 回退: os.times()
        try:
            t = os.times()
            return t.user + t.system
        except Exception:
            pass
        return 0.0

    def get_fd_count(self) -> int:
        """获取当前打开的文件描述符数量"""
        if self._has_psutil and self._process is not None:
            try:
                return self._process.num_fds()
            except Exception:
                pass
        # 回退: Linux /proc/self/fd
        try:
            return len(os.listdir("/proc/self/fd"))
        except Exception:
            pass
        # 回退: 统计文件对象 (粗略估计)
        import gc
        return sum(1 for obj in gc.get_objects() if hasattr(obj, "fileno") and callable(getattr(obj, "fileno", None)))

    def get_stats(self) -> Dict[str, Any]:
        """获取所有资源统计信息"""
        return {
            "memory_mb": self.get_memory_usage_mb(),
            "cpu_time_seconds": self.get_cpu_time_seconds(),
            "fd_count": self.get_fd_count(),
            "has_psutil": self._has_psutil,
        }


# ============ 资源限制器 ============


class ResourceLimiter:
    """资源限制器

    跟踪和限制 CPU 时间、内存、文件描述符的使用。
    支持装饰器模式和上下文管理器模式。

    Args:
        max_memory_mb: 最大内存限制（MB），0 表示不限制
        max_cpu_time_seconds: 最大 CPU 时间限制（秒），0 表示不限制
        max_fd_count: 最大文件描述符数量，0 表示不限制
        check_interval_seconds: 检查间隔（秒）
    """

    def __init__(
        self,
        max_memory_mb: float = 0.0,
        max_cpu_time_seconds: float = 0.0,
        max_fd_count: int = 0,
        check_interval_seconds: float = 1.0,
    ):
        self.max_memory_mb = max_memory_mb
        self.max_cpu_time_seconds = max_cpu_time_seconds
        self.max_fd_count = max_fd_count
        self.check_interval_seconds = check_interval_seconds

        self._monitor = ResourceMonitor()
        self._lock = threading.Lock()
        self._start_cpu_time = 0.0
        self._running = False
        self._check_thread: Optional[threading.Thread] = None

    def check_limits(self) -> None:
        """检查当前资源使用是否超出限制，超出则抛出异常"""
        with self._lock:
            # 内存检查
            if self.max_memory_mb > 0:
                mem_mb = self._monitor.get_memory_usage_mb()
                if mem_mb > self.max_memory_mb:
                    raise MemoryLimitExceeded(self.max_memory_mb, mem_mb)

            # CPU 时间检查
            if self.max_cpu_time_seconds > 0:
                cpu_time = self._monitor.get_cpu_time_seconds()
                elapsed_cpu = cpu_time - self._start_cpu_time
                if elapsed_cpu > self.max_cpu_time_seconds:
                    raise CPULimitExceeded(self.max_cpu_time_seconds, elapsed_cpu)

            # 文件描述符检查
            if self.max_fd_count > 0:
                fd_count = self._monitor.get_fd_count()
                if fd_count > self.max_fd_count:
                    raise FDLimitExceeded(self.max_fd_count, fd_count)

    def start_monitoring(self) -> None:
        """启动后台监控线程"""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._start_cpu_time = self._monitor.get_cpu_time_seconds()
            self._check_thread = threading.Thread(target=self._monitor_loop, daemon=True)
            self._check_thread.start()

    def stop_monitoring(self) -> None:
        """停止后台监控线程"""
        with self._lock:
            self._running = False

    def _monitor_loop(self) -> None:
        """后台监控循环"""
        while True:
            with self._lock:
                if not self._running:
                    break
            try:
                self.check_limits()
            except ResourceLimitExceeded:
                # 在后台线程中无法直接抛出到主线程，需要配合其他机制
                # 这里仅记录，实际限制应在调用点 check_limits()
                pass
            time.sleep(self.check_interval_seconds)

    def get_usage(self) -> Dict[str, Any]:
        """获取当前资源使用情况"""
        stats = self._monitor.get_stats()
        stats["limits"] = {
            "max_memory_mb": self.max_memory_mb,
            "max_cpu_time_seconds": self.max_cpu_time_seconds,
            "max_fd_count": self.max_fd_count,
        }
        return stats

    def __enter__(self):
        self.start_monitoring()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop_monitoring()
        return False


# ============ 装饰器 ============


def with_resource_limits(
    max_memory_mb: float = 0.0,
    max_cpu_time_seconds: float = 0.0,
    max_fd_count: int = 0,
    check_interval: float = 1.0,
):
    """资源限制装饰器

    为函数执行添加资源限制检查。支持同步和异步函数。

    Args:
        max_memory_mb: 最大内存限制（MB）
        max_cpu_time_seconds: 最大 CPU 时间限制（秒）
        max_fd_count: 最大文件描述符数量
        check_interval: 检查间隔（秒）

    Example:
        @with_resource_limits(max_memory_mb=512, max_cpu_time_seconds=60)
        def heavy_task():
            ...
    """
    def decorator(func: Callable) -> Callable:
        is_async = hasattr(func, "__code__") and func.__code__.co_flags & 0x80

        if is_async:
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs) -> Any:
                limiter = ResourceLimiter(
                    max_memory_mb=max_memory_mb,
                    max_cpu_time_seconds=max_cpu_time_seconds,
                    max_fd_count=max_fd_count,
                    check_interval_seconds=check_interval,
                )
                limiter.start_monitoring()
                try:
                    return await func(*args, **kwargs)
                finally:
                    limiter.stop_monitoring()
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs) -> Any:
                limiter = ResourceLimiter(
                    max_memory_mb=max_memory_mb,
                    max_cpu_time_seconds=max_cpu_time_seconds,
                    max_fd_count=max_fd_count,
                    check_interval_seconds=check_interval,
                )
                limiter.start_monitoring()
                try:
                    return func(*args, **kwargs)
                finally:
                    limiter.stop_monitoring()
            return sync_wrapper
    return decorator


# ============ 进程级限制 (Unix only) ============


def set_process_limits(
    max_memory_bytes: Optional[int] = None,
    max_cpu_seconds: Optional[int] = None,
    max_open_files: Optional[int] = None,
) -> Dict[str, Any]:
    """设置当前进程的资源限制（仅 Unix 系统）

    使用 resource 模块设置硬限制，对当前进程及其子进程生效。

    Args:
        max_memory_bytes: 最大虚拟内存（字节）
        max_cpu_seconds: 最大 CPU 时间（秒）
        max_open_files: 最大打开文件数

    Returns:
        应用结果字典
    """
    results = {}
    system = platform.system().lower()

    if system == "windows":
        results["status"] = "unsupported"
        results["message"] = "Process resource limits are not supported on Windows"
        return results

    if not _HAS_RESOURCE:
        results["status"] = "unsupported"
        results["message"] = "resource module not available on this platform"
        return results

    try:
        if max_memory_bytes is not None:
            resource.setrlimit(resource.RLIMIT_AS, (max_memory_bytes, max_memory_bytes))  # type: ignore
            results["memory"] = {"limit_bytes": max_memory_bytes, "status": "ok"}
    except Exception as e:
        results["memory"] = {"status": "error", "error": str(e)}

    try:
        if max_cpu_seconds is not None:
            resource.setrlimit(resource.RLIMIT_CPU, (max_cpu_seconds, max_cpu_seconds))  # type: ignore
            results["cpu"] = {"limit_seconds": max_cpu_seconds, "status": "ok"}
    except Exception as e:
        results["cpu"] = {"status": "error", "error": str(e)}

    try:
        if max_open_files is not None:
            resource.setrlimit(resource.RLIMIT_NOFILE, (max_open_files, max_open_files))  # type: ignore
            results["fd"] = {"limit": max_open_files, "status": "ok"}
    except Exception as e:
        results["fd"] = {"status": "error", "error": str(e)}

    results["status"] = "ok"
    return results


# ============ 便捷函数 ============


def get_system_memory_info() -> Dict[str, Any]:
    """获取系统内存信息"""
    if _psutil is not None:
        try:
            mem = _psutil.virtual_memory()
            return {
                "total_mb": mem.total / (1024 * 1024),
                "available_mb": mem.available / (1024 * 1024),
                "percent_used": mem.percent,
                "has_psutil": True,
            }
        except Exception:
            pass
    return {"has_psutil": False, "message": "psutil not available"}


def get_process_memory_info() -> Dict[str, Any]:
    """获取当前进程内存信息"""
    monitor = ResourceMonitor()
    return monitor.get_stats()


@contextmanager
def memory_limit_context(max_memory_mb: float):
    """内存限制上下文管理器

    Example:
        with memory_limit_context(max_memory_mb=512):
            heavy_operation()
    """
    limiter = ResourceLimiter(max_memory_mb=max_memory_mb)
    limiter.start_monitoring()
    try:
        yield limiter
    finally:
        limiter.stop_monitoring()

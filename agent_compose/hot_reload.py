"""
Hot Reload - 配置热重载模块

提供:
- 配置文件变更监控 (watchdog 或轮询回退)
- 热重载管理器
- 重载事件回调注册
- 防抖重载

设计原则:
- 零外部依赖核心 (纯 Python 轮询)
- 可选 watchdog 增强
- 线程安全
- 支持 agent.json / pipeline / YAML 配置
"""

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Union

# 可选的 watchdog 导入
_has_watchdog = False
try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileModifiedEvent
    _has_watchdog = True
except ImportError:
    pass


# ============ 文件变更检测 ============


class FileSnapshot:
    """文件快照，用于检测变更"""

    def __init__(self, path: str, mtime: float, size: int, content_hash: str):
        self.path = path
        self.mtime = mtime
        self.size = size
        self.content_hash = content_hash

    @classmethod
    def from_path(cls, path: str) -> Optional["FileSnapshot"]:
        try:
            stat = os.stat(path)
            with open(path, "rb") as f:
                content_hash = hashlib.md5(f.read()).hexdigest()
            return cls(path, stat.st_mtime, stat.st_size, content_hash)
        except Exception:
            return None

    def is_changed(self, other: "FileSnapshot") -> bool:
        return self.mtime != other.mtime or self.size != other.size or self.content_hash != other.content_hash


# ============ 配置监控器 ============


class ConfigWatcher:
    """配置变更监控器

    监控指定目录或文件的变更，支持 watchdog 或轮询回退。

    Args:
        paths: 监控路径列表（文件或目录）
        poll_interval: 轮询间隔（秒），watchdog 不可用时使用
        use_watchdog: 是否尝试使用 watchdog
        recursive: 是否递归监控子目录
    """

    def __init__(
        self,
        paths: List[str],
        poll_interval: float = 2.0,
        use_watchdog: bool = True,
        recursive: bool = True,
    ):
        self.paths = [str(p) for p in paths]
        self.poll_interval = poll_interval
        self.use_watchdog = use_watchdog and _has_watchdog
        self.recursive = recursive

        self._snapshots: Dict[str, FileSnapshot] = {}
        self._callbacks: List[Callable[[str], None]] = []
        self._lock = threading.Lock()
        self._running = False
        self._observer: Optional[Any] = None
        self._poll_thread: Optional[threading.Thread] = None

        # 初始化快照
        self._refresh_snapshots()

    def _refresh_snapshots(self) -> None:
        new_snapshots = {}
        for path in self.paths:
            if os.path.isfile(path):
                snap = FileSnapshot.from_path(path)
                if snap:
                    new_snapshots[path] = snap
            elif os.path.isdir(path):
                for root, _, files in os.walk(path):
                    for fname in files:
                        fpath = os.path.join(root, fname)
                        snap = FileSnapshot.from_path(fpath)
                        if snap:
                            new_snapshots[fpath] = snap
        self._snapshots = new_snapshots

    def _detect_changes(self) -> List[str]:
        changed = []
        current_paths = set()

        for path in self.paths:
            if os.path.isfile(path):
                current_paths.add(path)
                new_snap = FileSnapshot.from_path(path)
                old_snap = self._snapshots.get(path)
                if old_snap is None or new_snap is None or old_snap.is_changed(new_snap):
                    changed.append(path)
            elif os.path.isdir(path):
                for root, _, files in os.walk(path):
                    for fname in files:
                        fpath = os.path.join(root, fname)
                        current_paths.add(fpath)
                        new_snap = FileSnapshot.from_path(fpath)
                        old_snap = self._snapshots.get(fpath)
                        if old_snap is None or new_snap is None or old_snap.is_changed(new_snap):
                            changed.append(fpath)

        # 检测删除的文件
        for old_path in self._snapshots:
            if old_path not in current_paths:
                changed.append(old_path)

        self._refresh_snapshots()
        return changed

    def add_callback(self, callback: Callable[[str], None]) -> None:
        """添加变更回调函数

        Args:
            callback: 回调函数，接收变更文件路径作为参数
        """
        with self._lock:
            self._callbacks.append(callback)

    def remove_callback(self, callback: Callable[[str], None]) -> None:
        """移除变更回调函数"""
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

    def _notify(self, changed_paths: List[str]) -> None:
        with self._lock:
            callbacks = list(self._callbacks)
        for path in changed_paths:
            for cb in callbacks:
                try:
                    cb(path)
                except Exception:
                    pass

    def start(self) -> None:
        """启动监控"""
        with self._lock:
            if self._running:
                return
            self._running = True

        if self.use_watchdog:
            self._start_watchdog()
        else:
            self._start_polling()

    def stop(self) -> None:
        """停止监控"""
        with self._lock:
            self._running = False

        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join()
            except Exception:
                pass
            self._observer = None

    def _start_watchdog(self) -> None:
        if not _has_watchdog:
            self._start_polling()
            return

        class _Handler(FileSystemEventHandler):
            def __init__(self, watcher: "ConfigWatcher"):
                self.watcher = watcher

            def on_modified(self, event):
                if not event.is_directory:
                    self.watcher._notify([event.src_path])

            def on_created(self, event):
                if not event.is_directory:
                    self.watcher._notify([event.src_path])

        self._observer = Observer()
        handler = _Handler(self)
        for path in self.paths:
            if os.path.isdir(path):
                self._observer.schedule(handler, path, recursive=self.recursive)
            elif os.path.isfile(path):
                self._observer.schedule(handler, os.path.dirname(path) or ".", recursive=False)
        self._observer.start()

    def _start_polling(self) -> None:
        def poll_loop():
            while True:
                with self._lock:
                    if not self._running:
                        break
                changed = self._detect_changes()
                if changed:
                    self._notify(changed)
                time.sleep(self.poll_interval)

        self._poll_thread = threading.Thread(target=poll_loop, daemon=True)
        self._poll_thread.start()

    def is_running(self) -> bool:
        return self._running


# ============ 热重载管理器 ============


class HotReloadManager:
    """热重载管理器

    管理 agent.json / pipeline / YAML 配置的热重载，支持防抖。

    Args:
        config_paths: 配置文件路径列表
        debounce_seconds: 防抖时间（秒）
        poll_interval: 轮询间隔（秒）
        use_watchdog: 是否尝试使用 watchdog
    """

    def __init__(
        self,
        config_paths: List[str],
        debounce_seconds: float = 1.0,
        poll_interval: float = 2.0,
        use_watchdog: bool = True,
    ):
        self.config_paths = [str(p) for p in config_paths]
        self.debounce_seconds = debounce_seconds

        self._watcher = ConfigWatcher(
            paths=self.config_paths,
            poll_interval=poll_interval,
            use_watchdog=use_watchdog,
            recursive=True,
        )
        self._reload_callbacks: List[Callable[[str, Any], None]] = []
        self._lock = threading.Lock()
        self._debounce_timer: Optional[threading.Timer] = None
        self._pending_changes: Set[str] = set()
        self._loaded_configs: Dict[str, Any] = {}

        # 注册内部回调
        self._watcher.add_callback(self._on_file_changed)

    def add_reload_callback(self, callback: Callable[[str, Any], None]) -> None:
        """添加重载回调函数

        Args:
            callback: 回调函数，接收 (文件路径, 加载后的配置) 作为参数
        """
        with self._lock:
            self._reload_callbacks.append(callback)

    def remove_reload_callback(self, callback: Callable[[str, Any], None]) -> None:
        """移除重载回调函数"""
        with self._lock:
            if callback in self._reload_callbacks:
                self._reload_callbacks.remove(callback)

    def _on_file_changed(self, path: str) -> None:
        """文件变更内部处理（触发防抖）"""
        with self._lock:
            self._pending_changes.add(path)
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(self.debounce_seconds, self._do_reload)
            self._debounce_timer.start()

    def _do_reload(self) -> None:
        """执行实际重载"""
        with self._lock:
            changes = list(self._pending_changes)
            self._pending_changes.clear()
            self._debounce_timer = None
            callbacks = list(self._reload_callbacks)

        for path in changes:
            config = self._load_config(path)
            if config is not None:
                self._loaded_configs[path] = config
                for cb in callbacks:
                    try:
                        cb(path, config)
                    except Exception:
                        pass

    def _load_config(self, path: str) -> Any:
        """加载配置文件"""
        ext = os.path.splitext(path)[1].lower()
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            if ext == ".json":
                return json.loads(content)
            elif ext in (".yaml", ".yml"):
                try:
                    import yaml
                    return yaml.safe_load(content)
                except ImportError:
                    return {"error": "yaml module not installed", "raw": content}
            else:
                return {"raw": content}
        except Exception as e:
            return {"error": str(e), "path": path}

    def start(self) -> None:
        """启动热重载监控"""
        self._watcher.start()

    def stop(self) -> None:
        """停止热重载监控"""
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None
        self._watcher.stop()

    def get_loaded_config(self, path: str) -> Any:
        """获取已加载的配置"""
        return self._loaded_configs.get(path)

    def force_reload(self, path: str) -> Any:
        """强制重载指定配置"""
        config = self._load_config(path)
        if config is not None:
            self._loaded_configs[path] = config
            with self._lock:
                callbacks = list(self._reload_callbacks)
            for cb in callbacks:
                try:
                    cb(path, config)
                except Exception:
                    pass
        return config

    def is_running(self) -> bool:
        return self._watcher.is_running()


# ============ 便捷工厂函数 ============


def create_agent_reload_manager(
    project_dir: str,
    debounce_seconds: float = 1.0,
) -> HotReloadManager:
    """创建针对 agent-compose 项目的热重载管理器

    自动监控常见配置文件:
    - agent.json
    - configs/*.yml, configs/*.yaml
    - definitions/*.yml, definitions/*.yaml
    """
    paths = []
    base = Path(project_dir)

    # 常见配置文件
    for fname in ["agent.json", "pipeline.yaml", "pipeline.yml"]:
        fpath = base / fname
        if fpath.exists():
            paths.append(str(fpath))

    # 配置目录
    configs_dir = base / "configs"
    if configs_dir.exists():
        paths.append(str(configs_dir))

    # 定义目录
    definitions_dir = base / "definitions"
    if definitions_dir.exists():
        paths.append(str(definitions_dir))

    return HotReloadManager(
        config_paths=paths,
        debounce_seconds=debounce_seconds,
    )


def watch_file(path: str, callback: Callable[[str], None], poll_interval: float = 2.0) -> ConfigWatcher:
    """便捷函数: 监控单个文件变更

    Args:
        path: 文件路径
        callback: 变更回调
        poll_interval: 轮询间隔

    Returns:
        ConfigWatcher 实例（已启动）
    """
    watcher = ConfigWatcher(paths=[path], poll_interval=poll_interval, use_watchdog=True)
    watcher.add_callback(callback)
    watcher.start()
    return watcher

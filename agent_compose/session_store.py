"""
SessionStore - 会话状态持久化

支持三种后端:
  - memory: 内存存储（默认，适合单进程开发）
  - file:   文件系统存储（JSON 文件，适合单机持久化）
  - redis:  Redis 存储（适合多进程/分布式部署）

数据模型:
  SessionState:
    - session_id: str
    - agent_id: str
    - messages: list[dict]       # 对话历史
    - mcp_connections: list[str] # 已连接的 MCP server 名称
    - metadata: dict             # 自定义元数据
    - created_at: float
    - updated_at: float
"""
import json
import os
import time
import uuid
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from abc import ABC, abstractmethod


class SessionState:
    """会话状态数据模型"""

    def __init__(
        self,
        session_id: str,
        agent_id: str,
        messages: Optional[List[Dict[str, Any]]] = None,
        mcp_connections: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        created_at: Optional[float] = None,
        updated_at: Optional[float] = None,
    ):
        self.session_id = session_id
        self.agent_id = agent_id
        self.messages = messages or []
        self.mcp_connections = mcp_connections or []
        self.metadata = metadata or {}
        self.created_at = created_at or time.time()
        self.updated_at = updated_at or time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "messages": self.messages,
            "mcp_connections": self.mcp_connections,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionState":
        return cls(
            session_id=data["session_id"],
            agent_id=data["agent_id"],
            messages=data.get("messages", []),
            mcp_connections=data.get("mcp_connections", []),
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    def touch(self) -> None:
        """更新最后修改时间"""
        self.updated_at = time.time()


class SessionStoreBase(ABC):
    """会话存储抽象基类"""

    @abstractmethod
    def create(self, agent_id: str, metadata: Optional[Dict[str, Any]] = None) -> SessionState:
        """创建新会话"""
        ...

    @abstractmethod
    def get(self, session_id: str) -> Optional[SessionState]:
        """获取会话"""
        ...

    @abstractmethod
    def save(self, session: SessionState) -> None:
        """保存会话"""
        ...

    @abstractmethod
    def delete(self, session_id: str) -> bool:
        """删除会话，返回是否成功"""
        ...

    @abstractmethod
    def list_sessions(self, agent_id: Optional[str] = None) -> List[SessionState]:
        """列出会话，可按 agent_id 过滤"""
        ...

    @abstractmethod
    def cleanup(self, max_age_seconds: float = 3600) -> int:
        """清理过期会话，返回清理数量"""
        ...


class MemorySessionStore(SessionStoreBase):
    """内存会话存储"""

    def __init__(self):
        self._sessions: Dict[str, SessionState] = {}
        self._lock = threading.Lock()

    def create(self, agent_id: str, metadata: Optional[Dict[str, Any]] = None) -> SessionState:
        session_id = f"sess-{uuid.uuid4().hex[:12]}"
        session = SessionState(
            session_id=session_id,
            agent_id=agent_id,
            metadata=metadata or {},
        )
        with self._lock:
            self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Optional[SessionState]:
        with self._lock:
            return self._sessions.get(session_id)

    def save(self, session: SessionState) -> None:
        session.touch()
        with self._lock:
            self._sessions[session.session_id] = session

    def delete(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def list_sessions(self, agent_id: Optional[str] = None) -> List[SessionState]:
        with self._lock:
            sessions = list(self._sessions.values())
        if agent_id:
            sessions = [s for s in sessions if s.agent_id == agent_id]
        return sorted(sessions, key=lambda s: s.updated_at, reverse=True)

    def cleanup(self, max_age_seconds: float = 3600) -> int:
        now = time.time()
        to_delete = []
        with self._lock:
            for sid, session in self._sessions.items():
                if now - session.updated_at > max_age_seconds:
                    to_delete.append(sid)
            for sid in to_delete:
                del self._sessions[sid]
        return len(to_delete)


class FileSessionStore(SessionStoreBase):
    """文件系统会话存储"""

    def __init__(self, base_dir: str = "./.sessions"):
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _session_path(self, session_id: str) -> Path:
        return self._base_dir / f"{session_id}.json"

    def create(self, agent_id: str, metadata: Optional[Dict[str, Any]] = None) -> SessionState:
        session_id = f"sess-{uuid.uuid4().hex[:12]}"
        session = SessionState(
            session_id=session_id,
            agent_id=agent_id,
            metadata=metadata or {},
        )
        self.save(session)
        return session

    def get(self, session_id: str) -> Optional[SessionState]:
        path = self._session_path(session_id)
        if not path.exists():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return SessionState.from_dict(data)
        except (json.JSONDecodeError, KeyError):
            return None

    def save(self, session: SessionState) -> None:
        session.touch()
        path = self._session_path(session.session_id)
        with self._lock:
            tmp_path = path.with_suffix(".tmp")
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)
                tmp_path.replace(path)
            except Exception:
                if tmp_path.exists():
                    tmp_path.unlink()
                raise

    def delete(self, session_id: str) -> bool:
        path = self._session_path(session_id)
        with self._lock:
            if path.exists():
                path.unlink()
                return True
            return False

    def list_sessions(self, agent_id: Optional[str] = None) -> List[SessionState]:
        sessions = []
        for path in self._base_dir.glob("sess-*.json"):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                session = SessionState.from_dict(data)
                sessions.append(session)
            except (json.JSONDecodeError, KeyError):
                continue
        if agent_id:
            sessions = [s for s in sessions if s.agent_id == agent_id]
        return sorted(sessions, key=lambda s: s.updated_at, reverse=True)

    def cleanup(self, max_age_seconds: float = 3600) -> int:
        now = time.time()
        count = 0
        with self._lock:
            for path in list(self._base_dir.glob("sess-*.json")):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    updated = data.get("updated_at", 0)
                    if now - updated > max_age_seconds:
                        path.unlink()
                        count += 1
                except (json.JSONDecodeError, KeyError):
                    continue
        return count


class RedisSessionStore(SessionStoreBase):
    """Redis 会话存储（分布式场景）"""

    def __init__(self, redis_url: str = "redis://localhost:6379/0", prefix: str = "agent-compose:session:", ttl: int = 3600):
        self._redis_url = redis_url
        self._prefix = prefix
        self._ttl = ttl
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import redis
                self._client = redis.from_url(self._redis_url, decode_responses=True)
            except ImportError:
                raise RuntimeError("redis 包未安装，请执行: pip install redis")
        return self._client

    def _key(self, session_id: str) -> str:
        return f"{self._prefix}{session_id}"

    def create(self, agent_id: str, metadata: Optional[Dict[str, Any]] = None) -> SessionState:
        session_id = f"sess-{uuid.uuid4().hex[:12]}"
        session = SessionState(
            session_id=session_id,
            agent_id=agent_id,
            metadata=metadata or {},
        )
        self.save(session)
        return session

    def get(self, session_id: str) -> Optional[SessionState]:
        client = self._get_client()
        data = client.get(self._key(session_id))
        if not data:
            return None
        try:
            return SessionState.from_dict(json.loads(data))
        except (json.JSONDecodeError, KeyError):
            return None

    def save(self, session: SessionState) -> None:
        session.touch()
        client = self._get_client()
        client.setex(
            self._key(session.session_id),
            self._ttl,
            json.dumps(session.to_dict(), ensure_ascii=False),
        )

    def delete(self, session_id: str) -> bool:
        client = self._get_client()
        return bool(client.delete(self._key(session_id)))

    def list_sessions(self, agent_id: Optional[str] = None) -> List[SessionState]:
        client = self._get_client()
        pattern = f"{self._prefix}sess-*"
        sessions = []
        for key in client.scan_iter(match=pattern):
            data = client.get(key)
            if not data:
                continue
            try:
                session = SessionState.from_dict(json.loads(data))
                if agent_id and session.agent_id != agent_id:
                    continue
                sessions.append(session)
            except (json.JSONDecodeError, KeyError):
                continue
        return sorted(sessions, key=lambda s: s.updated_at, reverse=True)

    def cleanup(self, max_age_seconds: float = 3600) -> int:
        # Redis 使用 TTL 自动过期，此方法为空操作
        return 0


def create_session_store(
    backend: str = "memory",
    **kwargs,
) -> SessionStoreBase:
    """工厂方法：根据后端类型创建 SessionStore

    Args:
        backend: "memory" | "file" | "redis"
        **kwargs: 后端特定参数
            - file: base_dir (默认 "./.sessions")
            - redis: redis_url (默认 "redis://localhost:6379/0"), prefix, ttl

    Returns:
        SessionStoreBase 实例
    """
    if backend == "memory":
        return MemorySessionStore()
    elif backend == "file":
        return FileSessionStore(base_dir=kwargs.get("base_dir", "./.sessions"))
    elif backend == "redis":
        return RedisSessionStore(
            redis_url=kwargs.get("redis_url", "redis://localhost:6379/0"),
            prefix=kwargs.get("prefix", "agent-compose:session:"),
            ttl=kwargs.get("ttl", 3600),
        )
    else:
        raise ValueError(f"不支持的会话存储后端: {backend}，可选: memory, file, redis")

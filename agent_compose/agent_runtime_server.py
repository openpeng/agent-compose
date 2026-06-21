"""
AgentRuntimeServer - 服务化 Agent 运行时

将 AgentRuntime 从单次执行模式升级为常驻服务模式:
  - HTTP 接口暴露（创建会话 / 发送消息 / 销毁会话 / 健康检查）
  - 多会话并发（session_id 隔离）
  - 会话生命周期管理（创建 / 恢复 / 销毁 / 超时清理）
  - 对接 AgentOS（注册 / 心跳 / 状态上报）

HTTP 接口:
  POST /sessions              创建新会话
  GET  /sessions              列出所有会话
  GET  /sessions/:id           获取会话详情
  POST /sessions/:id/message   向会话发送消息
  DELETE /sessions/:id        销毁会话
  GET  /health                健康检查
  GET  /ready                 就绪检查（对接 K8s）
"""
import json
import os
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

from .agent_runtime import AgentRuntime
from .observability import Observability
from .session_store import (
    SessionState,
    SessionStoreBase,
    MemorySessionStore,
    create_session_store,
)


class AgentRuntimeServer:
    """Agent 运行时服务器

    管理多个 AgentRuntime 实例，每个实例对应一个会话。
    通过 HTTP 接口暴露会话管理能力。
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8080,
        session_store: Optional[SessionStoreBase] = None,
        session_backend: str = "memory",
        session_ttl: int = 3600,
        default_model_provider: str = "kimi",
        default_model_id: str = "moonshot-v1-128k",
        default_base_url: Optional[str] = None,
        agentos_url: Optional[str] = None,
        observability: Optional[Observability] = None,
    ):
        self.host = host
        self.port = port
        self.session_store = session_store or create_session_store(session_backend)
        self.session_ttl = session_ttl
        self.default_model_provider = default_model_provider
        self.default_model_id = default_model_id
        self.default_base_url = default_base_url

        # 运行时实例缓存: session_id -> AgentRuntime
        self._runtimes: Dict[str, AgentRuntime] = {}
        self._lock = threading.Lock()

        # AgentOS 集成
        self.agentos_url = agentos_url
        self._agentos_registered = False
        self._agentos_server_id: Optional[str] = None

        # 可观测性
        self.observability: Optional[Observability] = observability

        # 统计信息
        self._start_time = time.time()
        self._request_count = 0

    # ---------- 会话管理 ----------

    def create_session(
        self,
        agent_id: str,
        agent_json: Optional[Dict[str, Any]] = None,
        api_key: Optional[str] = None,
        model_provider: Optional[str] = None,
        model_id: Optional[str] = None,
        base_url: Optional[str] = None,
        webbridge_token: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """创建新会话

        Args:
            agent_id: Agent 标识（市场 ID 或本地名称）
            agent_json: Agent 配置（可选，如果不提供则从市场下载）
            api_key: LLM API Key
            model_provider: 模型提供商
            model_id: 模型 ID
            base_url: API 基础 URL
            webbridge_token: Kimi WebBridge Token
            metadata: 自定义元数据

        Returns:
            {"session_id": str, "agent_id": str, "status": "created"}
        """
        # 创建会话状态
        session = self.session_store.create(agent_id, metadata)

        # 创建 AgentRuntime 实例
        if agent_json:
            runtime = AgentRuntime(
                agent_id=agent_id,
                agent_json=agent_json,
                api_key=api_key,
                model_provider=model_provider or self.default_model_provider,
                model_id=model_id or self.default_model_id,
                base_url=base_url or self.default_base_url,
                webbridge_token=webbridge_token,
            )
        else:
            runtime = AgentRuntime.from_market(
                agent_id=agent_id,
                api_key=api_key,
                model_provider=model_provider or self.default_model_provider,
                model_id=model_id or self.default_model_id,
                base_url=base_url or self.default_base_url,
                webbridge_token=webbridge_token,
            )

        # 初始化 MCP 连接
        connected = runtime.initialize_mcps()
        session.mcp_connections = connected

        # 保存会话
        self.session_store.save(session)

        # 缓存运行时实例
        with self._lock:
            self._runtimes[session.session_id] = runtime

        # 可观测性: 记录会话创建
        if self.observability:
            self.observability.log_session_event(
                session.session_id, "session_created", {"agent_id": agent_id}
            )
            span = self.observability.tracer.start_span(
                "create_session", attributes={"agent_id": agent_id, "session_id": session.session_id}
            )
            self.observability.tracer.end_span(span)
            self.observability.metrics.counter("sessions_created_total", 1, {"agent_id": agent_id})

        return {
            "session_id": session.session_id,
            "agent_id": agent_id,
            "status": "created",
            "mcp_connections": connected,
            "created_at": session.created_at,
        }

    def send_message(
        self,
        session_id: str,
        message: str,
        max_turns: int = 15,
    ) -> Dict[str, Any]:
        """向会话发送消息

        Args:
            session_id: 会话 ID
            message: 用户消息
            max_turns: 最大工具调用轮次

        Returns:
            {"session_id": str, "history": list, "message_count": int}
        """
        runtime = self._get_runtime(session_id)
        if not runtime:
            return {"error": f"Session '{session_id}' not found"}

        session = self.session_store.get(session_id)
        if not session:
            return {"error": f"Session '{session_id}' state not found"}

        # 可观测性: 开始消息处理 span
        span = None
        start_time = time.time()
        if self.observability:
            span = self.observability.tracer.start_span(
                "send_message", attributes={"session_id": session_id}
            )
            self.observability.log_session_event(session_id, "message_received", {"message_length": len(message)})
            self.observability.metrics.counter("messages_received_total", 1, {"session_id": session_id})

        # 执行对话
        history = runtime.chat(message, max_turns=max_turns)

        # 更新会话状态
        session.messages.extend([
            {"role": "user", "content": message},
        ])
        for h in history:
            if h.get("type") == "assistant":
                session.messages.append({"role": "assistant", "content": h.get("content", "")})
        self.session_store.save(session)

        duration_ms = (time.time() - start_time) * 1000

        # 可观测性: 结束消息处理
        if self.observability:
            if span:
                self.observability.tracer.end_span(span)
            self.observability.log_session_event(
                session_id, "message_processed", {"duration_ms": round(duration_ms, 2), "history_length": len(history)}
            )
            self.observability.metrics.counter("messages_processed_total", 1, {"session_id": session_id})
            self.observability.metrics.histogram("message_processing_duration_ms", duration_ms, {"session_id": session_id})
            self.observability.profiler.record("send_message", duration_ms)

        return {
            "session_id": session_id,
            "history": history,
            "message_count": len(session.messages),
        }

    def destroy_session(self, session_id: str) -> Dict[str, Any]:
        """销毁会话"""
        runtime = self._get_runtime(session_id)
        if runtime:
            try:
                runtime.close_mcps()
            except Exception:
                pass

        with self._lock:
            self._runtimes.pop(session_id, None)

        deleted = self.session_store.delete(session_id)

        # 可观测性: 记录会话销毁
        if self.observability:
            self.observability.log_session_event(session_id, "session_destroyed", {"found": runtime is not None})
            self.observability.metrics.counter("sessions_destroyed_total", 1)

        return {
            "session_id": session_id,
            "status": "destroyed" if deleted else "not_found",
        }

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """获取会话详情"""
        session = self.session_store.get(session_id)
        if not session:
            return None
        return session.to_dict()

    def list_sessions(self, agent_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """列出会话"""
        sessions = self.session_store.list_sessions(agent_id)
        return [s.to_dict() for s in sessions]

    def restore_session(self, session_id: str) -> Dict[str, Any]:
        """恢复持久化的会话（重建 AgentRuntime 实例）"""
        session = self.session_store.get(session_id)
        if not session:
            return {"error": f"Session '{session_id}' not found"}

        # 如果运行时实例已存在，直接返回
        if session_id in self._runtimes:
            return {"session_id": session_id, "status": "already_active"}

        # 重建 AgentRuntime
        runtime = AgentRuntime.from_market(
            agent_id=session.agent_id,
            model_provider=session.metadata.get("model_provider", self.default_model_provider),
            model_id=session.metadata.get("model_id", self.default_model_id),
            base_url=session.metadata.get("base_url", self.default_base_url),
        )

        # 重新初始化 MCP
        connected = runtime.initialize_mcps()
        session.mcp_connections = connected

        with self._lock:
            self._runtimes[session_id] = runtime

        self.session_store.save(session)

        return {
            "session_id": session_id,
            "status": "restored",
            "mcp_connections": connected,
            "message_count": len(session.messages),
        }

    def cleanup_expired(self) -> int:
        """清理过期会话"""
        count = self.session_store.cleanup(self.session_ttl)
        # 同时清理运行时缓存
        with self._lock:
            active_ids = set(self._runtimes.keys())
            for sid in list(active_ids):
                if self.session_store.get(sid) is None:
                    runtime = self._runtimes.pop(sid, None)
                    if runtime:
                        try:
                            runtime.close_mcps()
                        except Exception:
                            pass
        return count

    # ---------- AgentOS 集成 ----------

    def register_with_agentos(self, agentos_url: str) -> Dict[str, Any]:
        """向 AgentOS 注册"""
        self.agentos_url = agentos_url
        # TODO: 实现 AgentOS 注册协议
        return {
            "status": "registered",
            "agentos_url": agentos_url,
            "message": "AgentOS registration placeholder — implement when AgentOS API is stable",
        }

    def health(self) -> Dict[str, Any]:
        """健康检查"""
        uptime = time.time() - self._start_time
        with self._lock:
            active_sessions = len(self._runtimes)
        result: Dict[str, Any] = {
            "status": "ok",
            "version": "1.0.0",
            "uptime_seconds": round(uptime, 1),
            "active_sessions": active_sessions,
            "total_requests": self._request_count,
            "agentos_registered": self._agentos_registered,
            "session_backend": type(self.session_store).__name__,
        }
        if self.observability:
            result["observability"] = self.observability.get_health_report()
        return result

    def setup_observability(self, service_name: str = "agent-compose") -> None:
        """初始化可观测性（如果尚未设置）"""
        if self.observability is None:
            self.observability = Observability(service_name=service_name)

    def ready(self) -> Dict[str, Any]:
        """就绪检查（对接 K8s readiness probe）"""
        return {
            "status": "ready",
            "session_store": "ok",
        }

    # ---------- 内部方法 ----------

    def _get_runtime(self, session_id: str) -> Optional[AgentRuntime]:
        with self._lock:
            return self._runtimes.get(session_id)

    # ---------- HTTP 服务 ----------

    def serve(self) -> None:
        """启动 HTTP 服务器"""
        handler = _make_handler(self)
        server = HTTPServer((self.host, self.port), handler)
        print(f"[AgentRuntimeServer] Listening on {self.host}:{self.port}")
        print(f"[AgentRuntimeServer] Endpoints:")
        print(f"  POST   /sessions              Create session")
        print(f"  GET    /sessions              List sessions")
        print(f"  GET    /sessions/:id           Get session")
        print(f"  POST   /sessions/:id/message   Send message")
        print(f"  DELETE /sessions/:id          Destroy session")
        print(f"  POST   /sessions/:id/restore   Restore session")
        print(f"  GET    /health                Health check")
        print(f"  GET    /ready                 Readiness check")

        # 启动过期清理线程
        cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        cleanup_thread.start()

        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n[AgentRuntimeServer] Shutting down...")
            # 清理所有运行时
            with self._lock:
                for sid, runtime in self._runtimes.items():
                    try:
                        runtime.close_mcps()
                    except Exception:
                        pass
                self._runtimes.clear()
            server.server_close()

    def _cleanup_loop(self) -> None:
        """定期清理过期会话"""
        while True:
            time.sleep(60)
            try:
                count = self.cleanup_expired()
                if count > 0:
                    print(f"[AgentRuntimeServer] Cleaned {count} expired session(s)")
            except Exception:
                pass


def _make_handler(server: AgentRuntimeServer):
    """创建 HTTP 请求处理器"""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # 静默日志，避免污染输出
            pass

        def _send_json(self, data: Any, status: int = 200):
            body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_body(self) -> Dict[str, Any]:
            length = int(self.headers.get("Content-Length", 0))
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            return json.loads(raw.decode("utf-8"))

        def _parse_path(self):
            parsed = urlparse(self.path)
            return parsed.path, parse_qs(parsed.query)

        def do_GET(self):
            server._request_count += 1
            path, _ = self._parse_path()

            if path == "/health":
                return self._send_json(server.health())
            if path == "/ready":
                return self._send_json(server.ready())
            if path == "/metrics":
                if server.observability:
                    metrics_text = server.observability.metrics.export_prometheus()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; version=0.0.4")
                    body = metrics_text.encode("utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                return self._send_json({"error": "Observability not enabled"}, 503)
            if path == "/sessions":
                agent_id = None
                # TODO: 从 query string 解析 agent_id
                return self._send_json(server.list_sessions(agent_id))
            if path.startswith("/sessions/"):
                session_id = path[len("/sessions/"):]
                session = server.get_session(session_id)
                if session is None:
                    return self._send_json({"error": "Session not found"}, 404)
                return self._send_json(session)

            self._send_json({"error": "Not Found"}, 404)

        def do_POST(self):
            server._request_count += 1
            path, _ = self._parse_path()

            try:
                body = self._read_body()
            except Exception:
                return self._send_json({"error": "Invalid JSON body"}, 400)

            if path == "/sessions":
                agent_id = body.get("agent_id", "")
                if not agent_id:
                    return self._send_json({"error": "agent_id is required"}, 400)
                result = server.create_session(
                    agent_id=agent_id,
                    agent_json=body.get("agent_json"),
                    api_key=body.get("api_key"),
                    model_provider=body.get("model_provider"),
                    model_id=body.get("model_id"),
                    base_url=body.get("base_url"),
                    webbridge_token=body.get("webbridge_token"),
                    metadata=body.get("metadata"),
                )
                return self._send_json(result, 201)

            if path.startswith("/sessions/") and path.endswith("/message"):
                parts = path[len("/sessions/"):]
                session_id = parts[: -len("/message")]
                message = body.get("message", "")
                if not message:
                    return self._send_json({"error": "message is required"}, 400)
                max_turns = body.get("max_turns", 15)
                result = server.send_message(session_id, message, max_turns)
                if "error" in result:
                    return self._send_json(result, 404)
                return self._send_json(result)

            if path.startswith("/sessions/") and path.endswith("/restore"):
                session_id = path[len("/sessions/"):]
                session_id = session_id[: -len("/restore")]
                result = server.restore_session(session_id)
                if "error" in result:
                    return self._send_json(result, 404)
                return self._send_json(result)

            self._send_json({"error": "Not Found"}, 404)

        def do_DELETE(self):
            server._request_count += 1
            path, _ = self._parse_path()

            if path.startswith("/sessions/"):
                session_id = path[len("/sessions/"):]
                result = server.destroy_session(session_id)
                return self._send_json(result)

            self._send_json({"error": "Not Found"}, 404)

    return Handler

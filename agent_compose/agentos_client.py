"""
AgentOS Client - AgentOS 运行时集成

提供与 AgentOS 平台的集成能力:
  - Agent 注册 / 注销
  - 心跳上报
  - 状态同步
  - 调度指令接收（启动 / 停止 / 重启 / 扩缩容）
  - 资源配额查询
  - 日志 / 指标上报

当前为接口定义 + mock 实现，待 AgentOS API 稳定后对接真实接口。
"""
import json
import os
import threading
import time
from typing import Any, Dict, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


class AgentOSClient:
    """AgentOS 平台客户端

    与 AgentOS 平台交互，实现 Agent 的注册、心跳、调度等能力。
    """

    def __init__(
        self,
        agentos_url: str = "http://localhost:9000",
        agent_id: Optional[str] = None,
        server_id: Optional[str] = None,
        api_key: Optional[str] = None,
        heartbeat_interval: int = 30,
    ):
        self.agentos_url = agentos_url.rstrip("/")
        self.agent_id = agent_id
        self.server_id = server_id or f"srv-{os.getpid()}"
        self.api_key = api_key or os.environ.get("AGENTOS_API_KEY", "")
        self.heartbeat_interval = heartbeat_interval

        self._registered = False
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._stop_heartbeat = threading.Event()

        # 状态
        self._status = "idle"
        self._active_sessions = 0
        self._last_heartbeat_ok = False

    # ---------- 注册 / 注销 ----------

    def register(
        self,
        agent_id: str,
        capabilities: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """向 AgentOS 注册 Agent

        Args:
            agent_id: Agent 标识
            capabilities: Agent 能力描述
            metadata: 元数据

        Returns:
            注册结果
        """
        self.agent_id = agent_id
        payload = {
            "agent_id": agent_id,
            "server_id": self.server_id,
            "capabilities": capabilities or {},
            "metadata": metadata or {},
            "api_key": self.api_key,
        }

        result = self._post("/api/v1/agents/register", payload)
        if result.get("status") == "ok":
            self._registered = True
            self._start_heartbeat()

        return result

    def unregister(self) -> Dict[str, Any]:
        """从 AgentOS 注销"""
        self._stop_heartbeat.set()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=5)

        result = self._post("/api/v1/agents/unregister", {
            "agent_id": self.agent_id,
            "server_id": self.server_id,
        })
        self._registered = False
        return result

    # ---------- 心跳 ----------

    def _start_heartbeat(self) -> None:
        """启动心跳线程"""
        self._stop_heartbeat.clear()
        self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._heartbeat_thread.start()

    def _heartbeat_loop(self) -> None:
        """心跳循环"""
        while not self._stop_heartbeat.is_set():
            try:
                result = self._post("/api/v1/agents/heartbeat", {
                    "agent_id": self.agent_id,
                    "server_id": self.server_id,
                    "status": self._status,
                    "active_sessions": self._active_sessions,
                })
                self._last_heartbeat_ok = result.get("status") == "ok"
            except Exception:
                self._last_heartbeat_ok = False

            self._stop_heartbeat.wait(self.heartbeat_interval)

    # ---------- 状态上报 ----------

    def report_status(self, status: str, active_sessions: int = 0) -> None:
        """更新状态（下次心跳时上报）"""
        self._status = status
        self._active_sessions = active_sessions

    def report_metrics(self, metrics: Dict[str, Any]) -> Dict[str, Any]:
        """上报指标"""
        return self._post("/api/v1/agents/metrics", {
            "agent_id": self.agent_id,
            "server_id": self.server_id,
            "metrics": metrics,
            "timestamp": time.time(),
        })

    def report_event(self, event_type: str, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """上报事件"""
        return self._post("/api/v1/agents/events", {
            "agent_id": self.agent_id,
            "server_id": self.server_id,
            "event_type": event_type,
            "event_data": event_data,
            "timestamp": time.time(),
        })

    # ---------- 调度指令 ----------

    def poll_commands(self) -> List[Dict[str, Any]]:
        """轮询调度指令"""
        result = self._get(f"/api/v1/agents/{self.agent_id}/commands")
        return result.get("commands", [])

    def acknowledge_command(self, command_id: str, result: Dict[str, Any]) -> Dict[str, Any]:
        """确认指令执行结果"""
        return self._post(f"/api/v1/agents/commands/{command_id}/ack", {
            "server_id": self.server_id,
            "result": result,
        })

    # ---------- 资源配额 ----------

    def get_quota(self) -> Dict[str, Any]:
        """查询资源配额"""
        return self._get(f"/api/v1/agents/{self.agent_id}/quota")

    # ---------- HTTP 通信 ----------

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """发送 POST 请求"""
        url = f"{self.agentos_url}{path}"
        try:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            if self.api_key:
                req.add_header("Authorization", f"Bearer {self.api_key}")

            with urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (URLError, HTTPError) as e:
            return {"status": "error", "error": str(e)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def _get(self, path: str) -> Dict[str, Any]:
        """发送 GET 请求"""
        url = f"{self.agentos_url}{path}"
        try:
            req = Request(url, method="GET")
            if self.api_key:
                req.add_header("Authorization", f"Bearer {self.api_key}")

            with urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (URLError, HTTPError) as e:
            return {"status": "error", "error": str(e)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ---------- 属性 ----------

    @property
    def is_registered(self) -> bool:
        return self._registered

    @property
    def last_heartbeat_ok(self) -> bool:
        return self._last_heartbeat_ok

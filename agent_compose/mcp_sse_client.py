"""MCP SSE 客户端 - 用于连接 Kimi WebBridge 等 SSE 类型的 MCP Server"""

import json
import time
import uuid
import threading
from typing import Any, Dict, List, Optional, Callable

try:
    import urllib.request
    import urllib.parse
except ImportError:
    pass


class SSEEvent:
    """SSE 事件"""

    def __init__(self, event_type: str = "", data: str = "", event_id: str = ""):
        self.event_type = event_type
        self.data = data
        self.event_id = event_id

    def as_json(self) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(self.data)
        except (json.JSONDecodeError, TypeError):
            return None

    def __repr__(self) -> str:
        return f"SSEEvent(type={self.event_type!r}, data={self.data[:100]!r})"


class MCPSSEClient:
    """MCP SSE 客户端，用于连接 SSE 类型的 MCP Server

    使用方法：
        client = MCPSSEClient("http://127.0.0.1:6001/sse", auth_token="xxx")
        client.connect()
        tools = client.list_tools()
        result = client.call_tool("page_navigate", {"url": "https://example.com"})
        client.close()
    """

    def __init__(self, sse_url: str, auth_token: str = "", timeout: int = 30):
        self.sse_url = sse_url
        self.auth_token = auth_token
        self.timeout = timeout

        self.session_id: Optional[str] = None
        self.post_endpoint: Optional[str] = None
        self._connected = False
        self._event_stream = None
        self._response = None
        self._pending_requests: Dict[str, threading.Event] = {}
        self._pending_results: Dict[str, Any] = {}
        self._tools_cache: Optional[List[Dict[str, Any]]] = None
        self._message_counter = 0
        self._lock = threading.Lock()

    # ---------- 连接管理 ----------

    def connect(self) -> bool:
        """连接到 MCP SSE Server"""
        try:
            headers = {"Accept": "text/event-stream"}
            if self.auth_token:
                headers["Authorization"] = f"Bearer {self.auth_token}"

            req = urllib.request.Request(self.sse_url, headers=headers)
            self._response = urllib.request.urlopen(req, timeout=self.timeout)
            self._event_stream = self._response
            self._connected = True

            # 等待初始化事件（包含 session_id 和 post endpoint）
            # 读取第一个 endpoint 事件
            self._read_initialization()

            # 发送 initialize 请求（MCP 协议要求）
            if self.session_id and self.post_endpoint:
                self._send_initialize()

            return True
        except Exception as e:
            print(f"[MCPSSEClient] 连接失败: {e}")
            self._connected = False
            return False

    def _read_initialization(self) -> None:
        """读取初始化信息：MCP Server 会先发送 session_id 和 endpoint"""
        try:
            buffer = b""
            # 读取前几个事件，寻找 endpoint/session 信息
            start_time = time.time()
            while time.time() - start_time < self.timeout:
                chunk = self._event_stream.read(4096)
                if not chunk:
                    time.sleep(0.1)
                    continue
                buffer += chunk

                # 解析 SSE 事件（按行）
                lines = buffer.decode("utf-8", errors="ignore").split("\n")
                if len(lines) < 2:
                    continue

                # 保留最后一段（可能不完整）
                buffer = lines[-1].encode("utf-8")

                current_event = SSEEvent()
                for line in lines[:-1]:
                    line = line.strip()
                    if not line:
                        self._process_init_event(current_event)
                        current_event = SSEEvent()
                    elif line.startswith("event:"):
                        current_event.event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        current_event.data += line[5:].strip()
                    elif line.startswith("id:"):
                        current_event.event_id = line[3:].strip()

                if self.session_id and self.post_endpoint:
                    break
        except Exception as e:
            print(f"[MCPSSEClient] 初始化读取失败: {e}")

    def _process_init_event(self, event: SSEEvent) -> None:
        if not event.data:
            return
        try:
            data = json.loads(event.data)
            # endpoint 事件：包含 sessionId 和 post endpoint
            if isinstance(data, dict):
                if "sessionId" in data:
                    self.session_id = data["sessionId"]
                    print(f"[MCPSSEClient] 获取到 sessionId: {self.session_id[:16]}...")
                if "endpoint" in data:
                    self.post_endpoint = data["endpoint"]
                    print(f"[MCPSSEClient] 获取到 post endpoint: {self.post_endpoint}")
                # 也可能是 "uri" 字段
                if "uri" in data and not self.post_endpoint:
                    self.post_endpoint = data["uri"]
                    print(f"[MCPSSEClient] 获取到 uri: {self.post_endpoint}")
        except (json.JSONDecodeError, TypeError):
            pass

    def _send_initialize(self) -> None:
        """发送 MCP initialize 请求"""
        try:
            init_msg = {
                "jsonrpc": "2.0",
                "id": "init_" + str(self._next_id()),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "agent-compose", "version": "1.0.0"},
                },
            }
            self._post_jsonrpc(init_msg)
            # 发送 initialized 通知
            notif = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
            self._post_jsonrpc(notif)
            print("[MCPSSEClient] MCP 协议初始化完成")
        except Exception as e:
            print(f"[MCPSSEClient] 初始化消息发送失败: {e}")

    def close(self) -> None:
        """关闭连接"""
        try:
            if self._event_stream:
                self._event_stream.close()
            if self._response:
                self._response.close()
        except Exception:
            pass
        self._connected = False
        print("[MCPSSEClient] 连接已关闭")

    # ---------- 工具发现 ----------

    def list_tools(self) -> List[Dict[str, Any]]:
        """获取可用工具列表"""
        if self._tools_cache:
            return self._tools_cache

        if not self._connected:
            print("[MCPSSEClient] 未连接，无法获取工具列表")
            return []

        try:
            req_id = f"tools_list_{self._next_id()}"
            msg = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/list",
            }

            result = self._post_jsonrpc(msg, wait_response=True, request_id=req_id)
            if isinstance(result, dict):
                tools = result.get("tools", [])
                self._tools_cache = tools
                print(f"[MCPSSEClient] 发现 {len(tools)} 个工具:")
                for t in tools:
                    print(f"  - {t.get('name', '?')}: {t.get('description', '')[:60]}")
                return tools

        except Exception as e:
            print(f"[MCPSSEClient] 获取工具列表失败: {e}")

        return []

    # ---------- 工具调用 ----------

    def call_tool(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """调用工具"""
        if not self._connected:
            return {"_error": "未连接到 MCP Server"}

        try:
            req_id = f"tool_call_{self._next_id()}"
            msg = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "tools/call",
                "params": {
                    "name": tool_name,
                    "arguments": arguments or {},
                },
            }

            result = self._post_jsonrpc(msg, wait_response=True, request_id=req_id)
            if isinstance(result, dict):
                return self._parse_tool_result(result)
            return {"_raw": result}
        except Exception as e:
            print(f"[MCPSSEClient] 调用工具 {tool_name} 失败: {e}")
            return {"_error": str(e)}

    def _parse_tool_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """解析工具调用结果"""
        content = result.get("content", [])
        if isinstance(content, list):
            parsed = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "text":
                        parsed.append(item.get("text", ""))
                    elif item.get("type") == "image":
                        parsed.append(f"[IMAGE data={item.get('data', '')[:50]}]")
                    else:
                        parsed.append(str(item))
            if parsed:
                return {"result": "\n".join(parsed), "_raw": result}
        return {"result": content, "_raw": result}

    # ---------- 消息收发 ----------

    def _post_jsonrpc(
        self,
        msg: Dict[str, Any],
        wait_response: bool = False,
        request_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        """发送 JSON-RPC 消息"""
        if not self.post_endpoint or not self.session_id:
            print("[MCPSSEClient] 缺少 endpoint 或 sessionId")
            return None

        try:
            post_url = self.post_endpoint
            # 拼接 sessionId
            if "?" not in post_url:
                post_url = f"{post_url}?sessionId={self.session_id}"

            headers = {"Content-Type": "application/json", "Accept": "application/json"}
            if self.auth_token:
                headers["Authorization"] = f"Bearer {self.auth_token}"

            data = json.dumps(msg).encode("utf-8")
            req = urllib.request.Request(post_url, data=data, headers=headers, method="POST")

            response = urllib.request.urlopen(req, timeout=self.timeout)
            response_text = response.read().decode("utf-8")

            if wait_response and request_id:
                try:
                    response_data = json.loads(response_text)
                    # 如果是直接返回的结果
                    if isinstance(response_data, dict):
                        if response_data.get("id") == request_id:
                            if "result" in response_data:
                                return response_data["result"]
                            if "error" in response_data:
                                return {"_error": response_data["error"]}
                except (json.JSONDecodeError, TypeError):
                    pass

                # SSE 模式下可能需要监听事件流
                sse_result = self._wait_for_sse_response(request_id)
                if sse_result is not None:
                    return sse_result
                return json.loads(response_text) if response_text else None

            if response_text:
                try:
                    return json.loads(response_text)
                except (json.JSONDecodeError, TypeError):
                    return {"response": response_text}
            return None

        except Exception as e:
            print(f"[MCPSSEClient] POST 失败: {e}")
            return {"_error": str(e)}

    def _wait_for_sse_response(self, request_id: str, timeout: int = 15) -> Optional[Dict[str, Any]]:
        """通过 SSE 事件流等待指定 ID 的响应"""
        try:
            # 简化版：我们重新读取一段时间的事件流
            buffer = b""
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    chunk = self._event_stream.read(4096)
                except Exception:
                    chunk = None
                if not chunk:
                    time.sleep(0.1)
                    continue
                buffer += chunk

                lines = buffer.decode("utf-8", errors="ignore").split("\n")
                if len(lines) < 2:
                    continue

                buffer = lines[-1].encode("utf-8")

                current_event = SSEEvent()
                for line in lines[:-1]:
                    line = line.strip()
                    if not line:
                        try:
                            data = json.loads(current_event.data) if current_event.data else None
                            if isinstance(data, dict) and data.get("id") == request_id:
                                if "result" in data:
                                    return data["result"]
                                if "error" in data:
                                    return {"_error": data["error"]}
                        except (json.JSONDecodeError, TypeError):
                            pass
                        current_event = SSEEvent()
                    elif line.startswith("event:"):
                        current_event.event_type = line[6:].strip()
                    elif line.startswith("data:"):
                        current_event.data += line[5:].strip()
                    elif line.startswith("id:"):
                        current_event.event_id = line[3:].strip()
        except Exception as e:
            print(f"[MCPSSEClient] 等待 SSE 响应失败: {e}")
        return None

    def _next_id(self) -> int:
        with self._lock:
            self._message_counter += 1
            return self._message_counter

    # ---------- 便捷方法 ----------

    def navigate(self, url: str) -> Dict[str, Any]:
        """导航到指定网页"""
        return self.call_tool("page_navigate", {"url": url})

    def extract_content(self, query: str = "") -> Dict[str, Any]:
        """提取当前页面内容"""
        return self.call_tool("page_extract", {"query": query} if query else {})

    def click(self, selector: str) -> Dict[str, Any]:
        """点击页面元素"""
        return self.call_tool("page_click", {"selector": selector})

    def fill_form(self, field: str, value: str) -> Dict[str, Any]:
        """填写表单字段"""
        return self.call_tool("page_fill", {"field": field, "value": value})

    def screenshot(self) -> Dict[str, Any]:
        """页面截图"""
        return self.call_tool("page_screenshot", {})

    def get_page_info(self) -> Dict[str, Any]:
        """获取当前页面信息（URL、标题等）"""
        return self.call_tool("page_info", {})

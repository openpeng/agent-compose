"""LLM API 客户端 - 支持 Kimi、OpenAI、Anthropic 等常见 API"""

import json
import time
import urllib.request
import urllib.parse
from typing import Any, Dict, List, Optional


class LLMClient:
    """通用 LLM API 客户端

    支持以下 provider：
    - kimi      (Kimi/Moonshot AI)
    - openai    (OpenAI Chat Completions)
    - anthropic (Claude)
    - deepseek  (DeepSeek)

    使用方法：
        client = LLMClient(
            provider="kimi",
            api_key="sk-xxx",
            model="mochi-v2-5",
            base_url="https://api.moonshot.cn/v1"
        )
        response = client.chat("你好！")
        print(response["content"])
    """

    PROVIDERS = {
        "kimi": {
            "default_model": "mochi-v2-5",
            "default_url": "https://api.moonshot.cn/v1",
            "supports_tools": True,
        },
        "openai": {
            "default_model": "gpt-4o-mini",
            "default_url": "https://api.openai.com/v1",
            "supports_tools": True,
        },
        "anthropic": {
            "default_model": "claude-sonnet-4-20250514",
            "default_url": "https://api.anthropic.com/v1",
            "supports_tools": True,
        },
        "deepseek": {
            "default_model": "deepseek-chat",
            "default_url": "https://api.deepseek.com/v1",
            "supports_tools": True,
        },
    }

    def __init__(
        self,
        provider: str = "kimi",
        api_key: str = "",
        model: str = "",
        base_url: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        timeout: int = 120,
    ):
        self.provider = provider.lower()
        if self.provider not in self.PROVIDERS:
            raise ValueError(f"不支持的 provider: {provider}。可用: {list(self.PROVIDERS.keys())}")

        provider_info = self.PROVIDERS[self.provider]
        self.api_key = api_key
        self.model = model or provider_info["default_model"]
        self.base_url = base_url or provider_info["default_url"]
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.conversation_history: List[Dict[str, str]] = []

    # ---------- 核心接口 ----------

    def reset(self) -> None:
        """清空对话历史"""
        self.conversation_history = []

    def chat(self, user_message: str, system_message: Optional[str] = None) -> Dict[str, Any]:
        """发送普通对话请求（不带工具调用）"""
        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.extend(self.conversation_history)
        messages.append({"role": "user", "content": user_message})

        response = self._send_request(messages, tools=None)
        assistant_message = response.get("content", "")
        self.conversation_history.append({"role": "user", "content": user_message})
        self.conversation_history.append({"role": "assistant", "content": assistant_message})

        return {
            "content": assistant_message,
            "tool_calls": response.get("tool_calls", []),
            "usage": response.get("usage", {}),
        }

    def chat_with_tools(
        self,
        user_message: str,
        tools: List[Dict[str, Any]],
        system_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """发送带工具调用能力的对话请求"""
        messages = []
        if system_message:
            messages.append({"role": "system", "content": system_message})
        messages.extend(self.conversation_history)
        messages.append({"role": "user", "content": user_message})

        response = self._send_request(messages, tools=tools)

        assistant_message = response.get("content", "")
        tool_calls = response.get("tool_calls", [])

        # 记录助手消息
        assistant_msg = {"role": "assistant", "content": assistant_message}
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls
        self.conversation_history.append({"role": "user", "content": user_message})
        self.conversation_history.append(assistant_msg)

        return {
            "content": assistant_message,
            "tool_calls": tool_calls,
            "usage": response.get("usage", {}),
        }

    def submit_tool_result(
        self,
        tool_call_id: str,
        tool_name: str,
        result_content: str,
    ) -> Dict[str, Any]:
        """提交工具执行结果，继续对话"""
        tool_result_msg = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": result_content,
        }
        self.conversation_history.append(tool_result_msg)

        response = self._send_request(self.conversation_history, tools=None)
        assistant_message = response.get("content", "")
        self.conversation_history.append({"role": "assistant", "content": assistant_message})

        return {
            "content": assistant_message,
            "tool_calls": response.get("tool_calls", []),
            "usage": response.get("usage", {}),
        }

    # ---------- 请求发送 ----------

    def _send_request(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """发送 HTTP 请求到 LLM API"""
        if self.provider == "anthropic":
            return self._send_anthropic_request(messages, tools)
        return self._send_openai_compatible_request(messages, tools)

    def _send_openai_compatible_request(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """OpenAI 兼容协议（Kimi / DeepSeek / OpenAI）"""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        # Kimi 特有 header
        if self.provider == "kimi":
            pass

        url = self.base_url.rstrip("/") + "/chat/completions"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            response = urllib.request.urlopen(req, timeout=self.timeout)
            response_text = response.read().decode("utf-8")
            result = json.loads(response_text)

            # 解析响应
            choices = result.get("choices", [])
            if not choices:
                return {"content": "", "tool_calls": [], "usage": result.get("usage", {})}

            msg = choices[0].get("message", {})
            content = msg.get("content", "") or ""
            tool_calls = msg.get("tool_calls", [])

            return {
                "content": content,
                "tool_calls": tool_calls,
                "usage": result.get("usage", {}),
            }
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="ignore")
            print(f"[LLMClient] HTTP {e.code} 错误: {error_body[:300]}")
            return {"content": f"[API 错误 {e.code}]", "tool_calls": [], "usage": {}}
        except urllib.error.URLError as e:
            print(f"[LLMClient] URL 错误: {e}")
            return {"content": f"[网络错误: {e}]", "tool_calls": [], "usage": {}}
        except Exception as e:
            print(f"[LLMClient] 请求异常: {e}")
            return {"content": f"[请求异常: {e}]", "tool_calls": [], "usage": {}}

    def _send_anthropic_request(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
    ) -> Dict[str, Any]:
        """Anthropic 协议 (简化版，转换为 OpenAI 格式)"""
        # 简化处理：转换消息格式并走同样的流程
        # 提取 system
        system_content = ""
        regular_messages = []
        for m in messages:
            if m.get("role") == "system":
                system_content = m.get("content", "")
            else:
                regular_messages.append(m)

        payload = {
            "model": self.model,
            "messages": regular_messages,
            "system": system_content,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        if tools:
            payload["tools"] = tools

        headers = {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }

        url = self.base_url.rstrip("/") + "/messages"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            response = urllib.request.urlopen(req, timeout=self.timeout)
            response_text = response.read().decode("utf-8")
            result = json.loads(response_text)

            content_parts = result.get("content", [])
            text_parts = []
            tool_calls = []
            for part in content_parts:
                if part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
                elif part.get("type") == "tool_use":
                    tool_calls.append({
                        "id": part.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": part.get("name", ""),
                            "arguments": json.dumps(part.get("input", {}), ensure_ascii=False),
                        },
                    })

            return {
                "content": "\n".join(text_parts),
                "tool_calls": tool_calls,
                "usage": {
                    "prompt_tokens": result.get("usage", {}).get("input_tokens", 0),
                    "completion_tokens": result.get("usage", {}).get("output_tokens", 0),
                },
            }
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="ignore")
            print(f"[LLMClient] Anthropic HTTP {e.code}: {error_body[:300]}")
            return {"content": f"[API 错误 {e.code}]", "tool_calls": [], "usage": {}}
        except Exception as e:
            print(f"[LLMClient] Anthropic 请求异常: {e}")
            return {"content": f"[请求异常: {e}]", "tool_calls": [], "usage": {}}

    # ---------- 状态查询 ----------

    def get_conversation(self) -> List[Dict[str, Any]]:
        """返回当前对话历史"""
        return list(self.conversation_history)

    def get_total_tokens(self) -> int:
        """估算总 token 数（简化版）"""
        total = 0
        for msg in self.conversation_history:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += len(content) // 4  # 粗略估算
        return total

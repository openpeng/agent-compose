"""Tests for agent_compose.llm_client module."""

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from agent_compose.llm_client import LLMClient


class TestLLMClientInit:
    """Tests for LLMClient initialization."""

    def test_init_valid_provider_kimi(self):
        client = LLMClient(provider="kimi", api_key="sk-test")
        assert client.provider == "kimi"
        assert client.model == "mochi-v2-5"
        assert client.base_url == "https://api.moonshot.cn/v1"
        assert client.api_key == "sk-test"

    def test_init_valid_provider_openai(self):
        client = LLMClient(provider="openai", api_key="sk-test")
        assert client.provider == "openai"
        assert client.model == "gpt-4o-mini"
        assert client.base_url == "https://api.openai.com/v1"

    def test_init_valid_provider_anthropic(self):
        client = LLMClient(provider="anthropic", api_key="sk-test")
        assert client.provider == "anthropic"
        assert client.model == "claude-sonnet-4-20250514"
        assert client.base_url == "https://api.anthropic.com/v1"

    def test_init_valid_provider_deepseek(self):
        client = LLMClient(provider="deepseek", api_key="sk-test")
        assert client.provider == "deepseek"
        assert client.model == "deepseek-chat"
        assert client.base_url == "https://api.deepseek.com/v1"

    def test_init_invalid_provider_raises(self):
        with pytest.raises(ValueError, match="不支持的 provider"):
            LLMClient(provider="unknown", api_key="sk-test")

    def test_init_custom_model_and_url(self):
        client = LLMClient(
            provider="kimi",
            api_key="sk-test",
            model="custom-model",
            base_url="https://custom.api.com/v1",
            temperature=0.5,
            max_tokens=2048,
            timeout=60,
        )
        assert client.model == "custom-model"
        assert client.base_url == "https://custom.api.com/v1"
        assert client.temperature == 0.5
        assert client.max_tokens == 2048
        assert client.timeout == 60

    def test_init_defaults(self):
        client = LLMClient(provider="openai", api_key="sk-test")
        assert client.temperature == 0.7
        assert client.max_tokens == 4096
        assert client.timeout == 120
        assert client.conversation_history == []


class TestLLMClientConversation:
    """Tests for conversation history management."""

    def test_reset_clears_history(self):
        client = LLMClient(provider="kimi", api_key="sk-test")
        client.conversation_history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        client.reset()
        assert client.conversation_history == []

    def test_get_conversation_returns_copy(self):
        client = LLMClient(provider="kimi", api_key="sk-test")
        client.conversation_history = [
            {"role": "user", "content": "hello"},
        ]
        history = client.get_conversation()
        assert history == client.conversation_history
        # Ensure it's a copy
        history.append({"role": "assistant", "content": "hi"})
        assert len(client.conversation_history) == 1

    def test_get_total_tokens_empty(self):
        client = LLMClient(provider="kimi", api_key="sk-test")
        assert client.get_total_tokens() == 0

    def test_get_total_tokens_estimation(self):
        client = LLMClient(provider="kimi", api_key="sk-test")
        # len("hello world") = 11, 11 // 4 = 2
        client.conversation_history = [
            {"role": "user", "content": "hello world"},
            {"role": "assistant", "content": "hello"},  # 5 // 4 = 1
        ]
        assert client.get_total_tokens() == 3


class TestLLMClientChat:
    """Tests for chat() method with mocked HTTP."""

    @patch("agent_compose.llm_client.urllib.request.urlopen")
    def test_chat_success(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": "Hello there!",
                            "role": "assistant",
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        ).encode("utf-8")
        mock_urlopen.return_value = mock_response

        client = LLMClient(provider="kimi", api_key="sk-test")
        result = client.chat("Say hello")

        assert result["content"] == "Hello there!"
        assert result["tool_calls"] == []
        assert result["usage"]["prompt_tokens"] == 10
        # Conversation history updated
        assert len(client.conversation_history) == 2
        assert client.conversation_history[0]["role"] == "user"
        assert client.conversation_history[0]["content"] == "Say hello"
        assert client.conversation_history[1]["role"] == "assistant"
        assert client.conversation_history[1]["content"] == "Hello there!"

    @patch("agent_compose.llm_client.urllib.request.urlopen")
    def test_chat_with_system_message(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": "System acknowledged.",
                            "role": "assistant",
                        }
                    }
                ],
                "usage": {},
            }
        ).encode("utf-8")
        mock_urlopen.return_value = mock_response

        client = LLMClient(provider="openai", api_key="sk-test")
        result = client.chat("Test", system_message="You are a tester.")

        assert result["content"] == "System acknowledged."

    @patch("agent_compose.llm_client.urllib.request.urlopen")
    def test_chat_empty_choices(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {"choices": [], "usage": {"prompt_tokens": 5}}
        ).encode("utf-8")
        mock_urlopen.return_value = mock_response

        client = LLMClient(provider="deepseek", api_key="sk-test")
        result = client.chat("Hello")

        assert result["content"] == ""
        assert result["usage"]["prompt_tokens"] == 5


class TestLLMClientChatWithTools:
    """Tests for chat_with_tools() method."""

    @patch("agent_compose.llm_client.urllib.request.urlopen")
    def test_chat_with_tools_returns_tool_calls(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": "I'll calculate that.",
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call_123",
                                    "type": "function",
                                    "function": {
                                        "name": "calculator",
                                        "arguments": '{"expression": "2+2"}',
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 15},
            }
        ).encode("utf-8")
        mock_urlopen.return_value = mock_response

        tools = [
            {
                "type": "function",
                "function": {
                    "name": "calculator",
                    "description": "Calculate math expression",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "expression": {"type": "string"}
                        },
                    },
                },
            }
        ]

        client = LLMClient(provider="kimi", api_key="sk-test")
        result = client.chat_with_tools("What is 2+2?", tools=tools)

        assert result["content"] == "I'll calculate that."
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["id"] == "call_123"
        assert result["tool_calls"][0]["function"]["name"] == "calculator"

        # History should contain tool_calls in assistant message
        assert len(client.conversation_history) == 2
        assistant_msg = client.conversation_history[1]
        assert "tool_calls" in assistant_msg


class TestLLMClientSubmitToolResult:
    """Tests for submit_tool_result() method."""

    @patch("agent_compose.llm_client.urllib.request.urlopen")
    def test_submit_tool_result_appends_and_calls_llm(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": "The result is 4.",
                            "role": "assistant",
                        }
                    }
                ],
                "usage": {},
            }
        ).encode("utf-8")
        mock_urlopen.return_value = mock_response

        client = LLMClient(provider="openai", api_key="sk-test")
        client.conversation_history = [
            {"role": "user", "content": "What is 2+2?"},
            {
                "role": "assistant",
                "content": "I'll calculate.",
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {"name": "calculator", "arguments": '{"expression": "2+2"}'},
                    }
                ],
            },
        ]

        result = client.submit_tool_result(
            tool_call_id="call_123",
            tool_name="calculator",
            result_content="4",
        )

        assert result["content"] == "The result is 4."
        # History should now have tool result + new assistant message
        assert len(client.conversation_history) == 4
        tool_msg = client.conversation_history[2]
        assert tool_msg["role"] == "tool"
        assert tool_msg["tool_call_id"] == "call_123"
        assert tool_msg["content"] == "4"


class TestLLMClientErrorHandling:
    """Tests for error handling in HTTP requests."""

    @patch("agent_compose.llm_client.urllib.request.urlopen")
    def test_chat_http_error_returns_error_content(self, mock_urlopen):
        mock_error = urllib.error.HTTPError(
            url="https://api.moonshot.cn/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=None,
        )
        # HTTPError.read() needs to return bytes; we mock it separately
        mock_error.read = MagicMock(return_value=b'{"error": "invalid key"}')
        mock_urlopen.side_effect = mock_error

        client = LLMClient(provider="kimi", api_key="bad-key")
        result = client.chat("Hello")

        assert "[API 错误 401]" in result["content"]
        assert result["tool_calls"] == []

    @patch("agent_compose.llm_client.urllib.request.urlopen")
    def test_chat_url_error_returns_network_error(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        client = LLMClient(provider="kimi", api_key="sk-test")
        result = client.chat("Hello")

        assert "[网络错误" in result["content"]
        assert result["tool_calls"] == []


class TestLLMClientAnthropicRequest:
    """Tests for _send_anthropic_request with mocked response."""

    @patch("agent_compose.llm_client.urllib.request.urlopen")
    def test_send_anthropic_request_text_response(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "content": [
                    {"type": "text", "text": "Hello from Claude!"}
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
        ).encode("utf-8")
        mock_urlopen.return_value = mock_response

        client = LLMClient(provider="anthropic", api_key="sk-ant-test")
        result = client._send_anthropic_request(
            messages=[{"role": "user", "content": "Hi"}],
            tools=None,
        )

        assert result["content"] == "Hello from Claude!"
        assert result["tool_calls"] == []
        assert result["usage"]["prompt_tokens"] == 10
        assert result["usage"]["completion_tokens"] == 5

    @patch("agent_compose.llm_client.urllib.request.urlopen")
    def test_send_anthropic_request_with_tool_use(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(
            {
                "content": [
                    {"type": "text", "text": "Let me calculate."},
                    {
                        "type": "tool_use",
                        "id": "toolu_123",
                        "name": "calculator",
                        "input": {"expression": "2+2"},
                    },
                ],
                "usage": {"input_tokens": 15, "output_tokens": 10},
            }
        ).encode("utf-8")
        mock_urlopen.return_value = mock_response

        client = LLMClient(provider="anthropic", api_key="sk-ant-test")
        result = client._send_anthropic_request(
            messages=[{"role": "user", "content": "Calculate 2+2"}],
            tools=None,
        )

        assert result["content"] == "Let me calculate."
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["id"] == "toolu_123"
        assert result["tool_calls"][0]["function"]["name"] == "calculator"
        assert result["tool_calls"][0]["function"]["arguments"] == '{"expression": "2+2"}'

    @patch("agent_compose.llm_client.urllib.request.urlopen")
    def test_send_anthropic_request_http_error(self, mock_urlopen):
        mock_error = urllib.error.HTTPError(
            url="https://api.anthropic.com/v1/messages",
            code=400,
            msg="Bad Request",
            hdrs={},
            fp=None,
        )
        mock_error.read = MagicMock(return_value=b'{"error": "bad request"}')
        mock_urlopen.side_effect = mock_error

        client = LLMClient(provider="anthropic", api_key="sk-ant-test")
        result = client._send_anthropic_request(
            messages=[{"role": "user", "content": "Hi"}],
            tools=None,
        )

        assert "[API 错误 400]" in result["content"]

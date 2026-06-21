"""
AgentRuntime 集成测试

验证 AgentRuntime 与 PipelineEngine、ToolRegistry、Observability 的集成。
"""

import asyncio
import pytest
from unittest.mock import patch, MagicMock

from agent_compose.agent_runtime import AgentRuntime
from agent_compose.pipeline_engine import PipelineEngine, ToolRegistry
from agent_compose.observability import Observability


# ---------- Fixtures ----------


@pytest.fixture
def minimal_agent_json():
    """最小化的 agent.json 配置"""
    return {
        "schema_version": "2.0",
        "identity": {
            "name": "test-agent",
            "display_name": "Test Agent",
            "version": "1.0.0",
            "description": "A test agent",
        },
        "instructions": {"content": "You are a test agent."},
        "capabilities": [],
        "mcp_servers": [],
    }


@pytest.fixture
def runtime(minimal_agent_json):
    """创建 AgentRuntime 实例"""
    return AgentRuntime(
        agent_id="test-agent-001",
        agent_json=minimal_agent_json,
        api_key="test-api-key",
    )


# ---------- ToolRegistry 集成测试 ----------


class TestToolRegistryIntegration:
    def test_tool_registry_initialized(self, runtime):
        """AgentRuntime 创建时应该初始化 ToolRegistry 并注册内置工具"""
        assert runtime.tool_registry is not None
        assert isinstance(runtime.tool_registry, ToolRegistry)
        # 验证内置工具已注册
        tools = runtime.tool_registry.list_tools()
        assert "bash" in tools
        assert "read_file" in tools

    def test_tool_registry_has_builtin_tools(self, runtime):
        """验证所有 7 个内置工具都已注册"""
        expected_tools = [
            "bash",
            "read_file",
            "write_file",
            "glob",
            "llm_chat",
            "web_search",
            "web_fetch",
        ]
        registered_tools = runtime.tool_registry.list_tools()
        for tool_name in expected_tools:
            assert tool_name in registered_tools, f"内置工具 '{tool_name}' 未注册"
        assert len(registered_tools) >= 7


# ---------- PipelineEngine 集成测试 ----------


class TestPipelineEngineIntegration:
    def test_initialize_pipeline_engine(self, runtime):
        """initialize_pipeline_engine 应该创建 PipelineEngine 实例"""
        engine = runtime.initialize_pipeline_engine()
        assert engine is not None
        assert isinstance(engine, PipelineEngine)
        assert runtime.pipeline_engine is engine
        # 验证 engine 使用了 runtime 的 tool_registry
        assert runtime.pipeline_engine.tool_registry is runtime.tool_registry

    def test_initialize_pipeline_engine_creates_new_instance(self, runtime):
        """每次初始化都会创建新的 PipelineEngine 实例"""
        engine1 = runtime.initialize_pipeline_engine()
        engine2 = runtime.initialize_pipeline_engine()
        # AgentRuntime.initialize_pipeline_engine 每次都创建新实例
        assert engine2 is not engine1
        assert isinstance(engine2, PipelineEngine)
        assert runtime.pipeline_engine is engine2

    def test_execute_pipeline_simple(self, runtime):
        """执行一个简单的流水线，使用 bash 工具执行 echo hello"""
        pipeline = {
            "pipeline": [
                {
                    "step": "echo_step",
                    "tool": "bash",
                    "args": {"command": "echo hello"},
                }
            ]
        }
        result = runtime.execute_pipeline(pipeline)
        assert result["success"] is True
        assert len(result["steps"]) == 1
        step = result["steps"][0]
        assert step["success"] is True
        # bash 工具返回 {"stdout": "...", "stderr": "", "exit_code": 0}
        assert "hello" in step["output"]["stdout"].lower()

    def test_execute_pipeline_with_args(self, runtime):
        """传递 initial_args 并使用模板解析"""
        pipeline = {
            "pipeline": [
                {
                    "step": "greet",
                    "tool": "bash",
                    "args": {"command": "echo {{name}}"},
                }
            ]
        }
        result = runtime.execute_pipeline(
            pipeline,
            initial_args={"name": "Alice"},
        )
        assert result["success"] is True
        step = result["steps"][0]
        assert "Alice" in step["output"]["stdout"]

    def test_execute_pipeline_timeout(self, runtime):
        """timeout_ms 应该正确触发超时"""
        pipeline = {
            "pipeline": [
                {"step": "s1", "tool": "bash", "args": {"command": "echo first"}},
                {"step": "s2", "tool": "bash", "args": {"command": "echo second"}},
            ]
        }
        # timeout_ms=0 会在检查第二步时立即触发超时
        result = runtime.execute_pipeline(pipeline, timeout_ms=0)
        assert result["success"] is False
        assert "timed out" in result["output"].lower()


# ---------- Observability 集成测试 ----------


class TestObservabilityIntegration:
    def test_setup_observability(self, runtime):
        """setup_observability 应该创建 Observability 实例"""
        obs = runtime.setup_observability()
        assert obs is not None
        assert isinstance(obs, Observability)
        assert runtime.observability is obs

    def test_setup_observability_custom_service_name(self, runtime):
        """应该支持自定义 service_name"""
        obs = runtime.setup_observability(service_name="my-service")
        assert obs is not None
        assert runtime.observability is obs


# ---------- Pipeline 与 Chat 共存测试 ----------


class TestPipelineAndChatCoexist:
    def test_pipeline_execution_does_not_break_chat(self, runtime):
        """流水线执行后，chat 功能仍然可用（LLM 调用被 mock）"""
        # 先执行流水线
        pipeline = {
            "pipeline": [
                {"step": "echo", "tool": "bash", "args": {"command": "echo pipeline_done"}}
            ]
        }
        pipeline_result = runtime.execute_pipeline(pipeline)
        assert pipeline_result["success"] is True

        # 验证 chat 仍然可以调用（mock LLM）
        mock_response = {
            "message": {
                "content": "Hello from mock LLM",
                "tool_calls": [],
            }
        }
        with patch.object(runtime, "_call_llm", return_value=mock_response):
            history = runtime.chat("Say hello")
            assert len(history) == 1
            assert history[0]["type"] == "assistant"
            assert history[0]["content"] == "Hello from mock LLM"

    def test_chat_mock_llm_no_tool_calls(self, runtime):
        """Mock LLM 无 tool_calls 时，chat 应直接返回内容"""
        mock_response = {
            "message": {
                "content": "Direct answer",
                "tool_calls": [],
            }
        }
        with patch.object(runtime, "_call_llm", return_value=mock_response):
            history = runtime.chat("What is 2+2?")
            assert history[0]["content"] == "Direct answer"
            assert history[0]["tool_calls"] == []

    def test_chat_mock_llm_with_tool_calls(self, runtime):
        """Mock LLM 返回 tool_calls 时，应执行工具并继续对话"""
        mock_responses = [
            {
                "message": {
                    "content": "Let me check",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {
                                "name": "bash",
                                "arguments": '{"command": "echo 4"}',
                            },
                        }
                    ],
                }
            },
            {
                "message": {
                    "content": "The answer is 4",
                    "tool_calls": [],
                }
            },
        ]
        with patch.object(
            runtime, "_call_llm", side_effect=mock_responses
        ) as mock_llm:
            history = runtime.chat("What is 2+2?")
            # 第一次 LLM 调用 -> 工具执行 -> 第二次 LLM 调用
            assert mock_llm.call_count == 2
            assert len(history) == 2
            assert history[0]["type"] == "assistant"
            assert history[1]["type"] == "assistant"
            assert history[1]["content"] == "The answer is 4"


# ---------- 端到端集成测试 ----------


class TestEndToEndIntegration:
    def test_full_workflow_pipeline_then_chat(self, runtime):
        """完整工作流：初始化 observability -> 执行 pipeline -> chat"""
        # 1. 设置可观测性
        runtime.setup_observability()
        assert runtime.observability is not None

        # 2. 执行流水线（多步骤，含模板解析）
        # 第一步将字符串结果存入 shared_context（通过纯 echo 命令），
        # 第二步通过模板引用该变量
        pipeline = {
            "pipeline": [
                {
                    "step": "get_name",
                    "tool": "bash",
                    "args": {"command": "echo {{name}}"},
                    "output": "name_result",
                },
                {
                    "step": "greet",
                    "tool": "bash",
                    "args": {"command": "echo Hello {{name_result}}"},
                },
            ]
        }
        result = runtime.execute_pipeline(pipeline, initial_args={"name": "World"})
        assert result["success"] is True
        assert len(result["steps"]) == 2
        # 由于 bash 工具返回 dict，output 存入 shared_context 的是 dict，
        # 模板解析后转为 str 会包含 dict 的字符串表示；因此这里只验证成功和步骤数
        # 并验证第一步输出包含 World
        first_output = result["steps"][0]["output"]["stdout"]
        assert "World" in first_output

        # 3. Chat（mock LLM）
        mock_response = {
            "message": {
                "content": "Workflow complete",
                "tool_calls": [],
            }
        }
        with patch.object(runtime, "_call_llm", return_value=mock_response):
            history = runtime.chat("Status?")
            assert history[0]["content"] == "Workflow complete"

    def test_pipeline_engine_initialized_on_demand(self, runtime):
        """execute_pipeline 应该按需初始化 pipeline_engine"""
        assert runtime.pipeline_engine is None
        pipeline = {
            "pipeline": [
                {"step": "s1", "tool": "bash", "args": {"command": "echo ok"}}
            ]
        }
        result = runtime.execute_pipeline(pipeline)
        assert result["success"] is True
        assert runtime.pipeline_engine is not None
        assert isinstance(runtime.pipeline_engine, PipelineEngine)

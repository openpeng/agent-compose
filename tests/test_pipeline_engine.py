"""
Pipeline Engine 单元测试
"""

import asyncio
import os
import pytest

from agent_compose.pipeline_engine import (
    PipelineEngine,
    ExecutionContext,
    StepResult,
    TemplateResolver,
    ToolRegistry,
)


# ============ StepResult Tests ============


class TestStepResult:
    def test_success_result(self):
        r = StepResult("test", True, {"data": "hello"})
        assert r.step_name == "test"
        assert r.success is True
        assert r.output == {"data": "hello"}
        assert r.to_dict()["success"] is True

    def test_failure_result(self):
        r = StepResult("test", False, error="Something went wrong")
        assert r.success is False
        assert r.error == "Something went wrong"
        assert r.output is None

    def test_to_dict(self):
        r = StepResult("step1", True, {"key": "value"}, duration_ms=100.5)
        d = r.to_dict()
        assert d["step"] == "step1"
        assert d["success"] is True
        assert d["output"] == {"key": "value"}
        assert d["duration_ms"] == 100.5
        assert d["error"] is None


# ============ ExecutionContext Tests ============


class TestExecutionContext:
    def test_init(self):
        ctx = ExecutionContext()
        assert ctx.cwd == os.getcwd()
        assert ctx.steps == {}
        assert ctx.shared_context == {}
        # env defaults to os.environ copy, not empty dict
        assert isinstance(ctx.env, dict)

    def test_set_step_result(self):
        ctx = ExecutionContext()
        r = StepResult("step1", True, "output")
        ctx.set_step_result("step1", r)
        assert "step1" in ctx.steps
        assert ctx.steps["step1"].output == "output"

    def test_set_shared(self):
        ctx = ExecutionContext()
        ctx.set_shared("key", "value")
        assert ctx.shared_context["key"] == "value"

    def test_env_variables(self):
        ctx = ExecutionContext()
        ctx.env["MY_VAR"] = "hello"
        assert ctx.env["MY_VAR"] == "hello"


# ============ TemplateResolver Tests ============


class TestTemplateResolver:
    def test_resolve_simple_variable(self):
        ctx = ExecutionContext()
        ctx.set_shared("greeting", "hello")
        resolver = TemplateResolver(ctx)
        assert resolver.resolve("{{greeting}}") == "hello"

    def test_resolve_steps_output(self):
        ctx = ExecutionContext()
        ctx.set_step_result("step1", StepResult("step1", True, "result_data"))
        resolver = TemplateResolver(ctx)
        assert resolver.resolve("{{steps.step1.output}}") == "result_data"

    def test_resolve_steps_success(self):
        ctx = ExecutionContext()
        ctx.set_step_result("step1", StepResult("step1", True, None))
        resolver = TemplateResolver(ctx)
        assert resolver.resolve("{{steps.step1.success}}") is True

    def test_resolve_env(self):
        ctx = ExecutionContext()
        ctx.env["TEST_VAR"] = "env_value"
        resolver = TemplateResolver(ctx)
        assert resolver.resolve("{{env.TEST_VAR}}") == "env_value"

    def test_resolve_shared_context(self):
        ctx = ExecutionContext()
        ctx.set_shared("data", {"nested": "value"})
        resolver = TemplateResolver(ctx)
        assert resolver.resolve("{{shared_context.data}}") == {"nested": "value"}

    def test_resolve_nonexistent(self):
        ctx = ExecutionContext()
        resolver = TemplateResolver(ctx)
        assert resolver.resolve("{{nonexistent}}") == "{{nonexistent}}"

    def test_resolve_dict(self):
        ctx = ExecutionContext()
        ctx.set_shared("name", "world")
        resolver = TemplateResolver(ctx)
        data = {"message": "Hello {{name}}!"}
        result = resolver.resolve(data)
        assert result["message"] == "Hello world!"

    def test_resolve_list(self):
        ctx = ExecutionContext()
        ctx.set_shared("item", "x")
        resolver = TemplateResolver(ctx)
        data = ["{{item}}", "static"]
        result = resolver.resolve(data)
        assert result == ["x", "static"]

    def test_resolve_nested(self):
        ctx = ExecutionContext()
        ctx.set_shared("val", "test")
        resolver = TemplateResolver(ctx)
        data = {"outer": ["{{val}}", {"inner": "{{val}}"}]}
        result = resolver.resolve(data)
        assert result["outer"] == ["test", {"inner": "test"}]


# ============ ToolRegistry Tests ============


class TestToolRegistry:
    def test_register_and_get(self):
        registry = ToolRegistry()
        async def my_tool(args, ctx):
            return "done"
        registry.register("my_tool", my_tool)
        assert registry.get("my_tool") == my_tool

    def test_get_nonexistent(self):
        registry = ToolRegistry()
        assert registry.get("missing") is None

    def test_parent_inheritance(self):
        parent = ToolRegistry()
        async def parent_tool(args, ctx):
            return "parent"
        parent.register("shared", parent_tool)

        child = ToolRegistry(parent)
        async def child_tool(args, ctx):
            return "child"
        child.register("child_only", child_tool)

        assert child.get("shared") == parent_tool
        assert child.get("child_only") == child_tool
        assert parent.get("child_only") is None

    def test_child_override_parent(self):
        parent = ToolRegistry()
        async def parent_tool(args, ctx):
            return "parent"
        parent.register("tool", parent_tool)

        child = ToolRegistry(parent)
        async def child_tool(args, ctx):
            return "child"
        child.register("tool", child_tool)

        assert child.get("tool") == child_tool

    def test_list_tools(self):
        registry = ToolRegistry()
        registry.register("tool_a", lambda a, c: None)
        registry.register("tool_b", lambda a, c: None)
        tools = registry.list_tools()
        assert "tool_a" in tools
        assert "tool_b" in tools


# ============ PipelineEngine Tests ============


class TestPipelineEngine:
    @pytest.fixture
    def engine(self):
        return PipelineEngine()

    @pytest.fixture
    def registry(self):
        reg = ToolRegistry()
        async def echo_tool(args, ctx):
            return args.get("message", "")
        reg.register("echo", echo_tool)

        async def fail_tool(args, ctx):
            raise RuntimeError("intentional failure")
        reg.register("fail", fail_tool)

        async def add_tool(args, ctx):
            a = args.get("a", 0)
            b = args.get("b", 0)
            return a + b
        reg.register("add", add_tool)

        return reg

    @pytest.mark.asyncio
    async def test_execute_single_step(self, engine, registry):
        pipeline = {
            "pipeline": [
                {"step": "s1", "tool": "echo", "args": {"message": "hello"}}
            ]
        }
        context = ExecutionContext()
        result = await engine.execute(pipeline, context, registry=registry)
        assert result["success"] is True
        assert result["output"] == "hello"
        assert len(result["steps"]) == 1

    @pytest.mark.asyncio
    async def test_execute_multiple_steps(self, engine, registry):
        pipeline = {
            "pipeline": [
                {"step": "s1", "tool": "echo", "args": {"message": "hello"}},
                {"step": "s2", "tool": "echo", "args": {"message": "world"}},
            ]
        }
        context = ExecutionContext()
        result = await engine.execute(pipeline, context, registry=registry)
        assert result["success"] is True
        assert len(result["steps"]) == 2

    @pytest.mark.asyncio
    async def test_execute_with_result_mapping(self, engine, registry):
        pipeline = {
            "pipeline": [
                {"step": "s1", "tool": "echo", "args": {"message": "hello"}, "result": "greeting"},
            ]
        }
        context = ExecutionContext()
        result = await engine.execute(pipeline, context, registry=registry)
        assert result["success"] is True
        assert context.shared_context.get("greeting") == "hello"

    @pytest.mark.asyncio
    async def test_execute_with_template_resolution(self, engine, registry):
        pipeline = {
            "pipeline": [
                {"step": "s1", "tool": "echo", "args": {"message": "hello"}, "result": "greeting"},
                {"step": "s2", "tool": "echo", "args": {"message": "{{greeting}} world"}},
            ]
        }
        context = ExecutionContext()
        result = await engine.execute(pipeline, context, registry=registry)
        assert result["success"] is True
        assert result["output"] == "hello world"

    @pytest.mark.asyncio
    async def test_execute_failure_abort(self, engine, registry):
        pipeline = {
            "pipeline": [
                {"step": "s1", "tool": "fail", "args": {}},
            ]
        }
        context = ExecutionContext()
        result = await engine.execute(pipeline, context, registry=registry)
        assert result["success"] is False
        assert "failure" in result["output"].lower() or "intentional" in result["output"].lower()

    @pytest.mark.asyncio
    async def test_execute_failure_continue(self, engine, registry):
        pipeline = {
            "pipeline": [
                {"step": "s1", "tool": "fail", "args": {}, "on_fail": "continue"},
                {"step": "s2", "tool": "echo", "args": {"message": "after failure"}},
            ]
        }
        context = ExecutionContext()
        result = await engine.execute(pipeline, context, registry=registry)
        assert result["success"] is True
        assert result["output"] == "after failure"
        assert len(result["steps"]) == 2

    @pytest.mark.asyncio
    async def test_execute_parallel_steps(self, engine, registry):
        pipeline = {
            "pipeline": [
                {
                    "step": "parallel_step",
                    "parallel": [
                        {"step": "p1", "tool": "echo", "args": {"message": "a"}},
                        {"step": "p2", "tool": "echo", "args": {"message": "b"}},
                    ]
                }
            ]
        }
        context = ExecutionContext()
        result = await engine.execute(pipeline, context, registry=registry)
        assert result["success"] is True
        assert len(result["steps"]) == 1

    @pytest.mark.asyncio
    async def test_execute_condition_true(self, engine, registry):
        pipeline = {
            "pipeline": [
                {"step": "s1", "tool": "echo", "args": {"message": "hello"}, "result": "greeting"},
                {"step": "s2", "tool": "echo", "args": {"message": "conditional"}, "when": "{{greeting}} == hello"},
            ]
        }
        context = ExecutionContext()
        result = await engine.execute(pipeline, context, registry=registry)
        assert result["success"] is True
        assert len(result["steps"]) == 2

    @pytest.mark.asyncio
    async def test_execute_condition_false(self, engine, registry):
        pipeline = {
            "pipeline": [
                {"step": "s1", "tool": "echo", "args": {"message": "hello"}, "result": "greeting"},
                {"step": "s2", "tool": "echo", "args": {"message": "conditional"}, "when": "{{greeting}} == world"},
            ]
        }
        context = ExecutionContext()
        result = await engine.execute(pipeline, context, registry=registry)
        assert result["success"] is True
        assert len(result["steps"]) == 1

    @pytest.mark.asyncio
    async def test_execute_timeout(self, engine, registry):
        # Timeout is checked before each step execution
        # Use a pipeline with multiple steps and a very short timeout
        # to ensure timeout triggers between steps
        pipeline = {
            "pipeline": [
                {"step": "s1", "tool": "echo", "args": {"message": "first"}},
                {"step": "s2", "tool": "echo", "args": {"message": "second"}},
                {"step": "s3", "tool": "echo", "args": {"message": "third"}},
            ]
        }
        context = ExecutionContext()
        # timeout_ms=0 means immediate timeout on next step check
        result = await engine.execute(pipeline, context, registry=registry, timeout_ms=0)
        assert result["success"] is False
        assert "timed out" in result["output"].lower()

    @pytest.mark.asyncio
    async def test_empty_pipeline(self, engine, registry):
        pipeline = {"pipeline": []}
        context = ExecutionContext()
        result = await engine.execute(pipeline, context, registry=registry)
        assert result["success"] is True
        assert result["output"] is None
        assert result["steps"] == []

    @pytest.mark.asyncio
    async def test_missing_tool(self, engine, registry):
        pipeline = {
            "pipeline": [
                {"step": "s1", "tool": "nonexistent", "args": {}}
            ]
        }
        context = ExecutionContext()
        result = await engine.execute(pipeline, context, registry=registry)
        assert result["success"] is False

"""
PipelineEngine - 流水线执行引擎

参考 agent-deploy 的 PipelineEngine 设计，在 Python 中重新实现。
支持步骤类型: tool / invoke / parallel / condition
支持错误处理策略: abort / skip / continue / retry
支持模板变量解析: {{var}} / {{steps.name.output}} / {{shared_context.key}} / {{env.VAR}}
"""
import asyncio
import json
import os
import re
import time
from typing import Any, Callable, Dict, List, Optional, Union


class StepResult:
    """步骤执行结果"""

    def __init__(
        self,
        step_name: str,
        success: bool,
        output: Any = None,
        error: Optional[str] = None,
        duration_ms: float = 0,
    ):
        self.step_name = step_name
        self.success = success
        self.output = output
        self.error = error
        self.duration_ms = duration_ms

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step": self.step_name,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


class ExecutionContext:
    """执行上下文"""

    def __init__(
        self,
        agent_id: str = "",
        initial_args: Optional[Dict[str, Any]] = None,
        shared_context: Optional[Dict[str, Any]] = None,
        env: Optional[Dict[str, str]] = None,
        cwd: Optional[str] = None,
    ):
        self.agent_id = agent_id
        self.initial_args = initial_args or {}
        self.shared_context = shared_context or {}
        self.steps: Dict[str, StepResult] = {}
        self.env = env or dict(os.environ)
        self.cwd = cwd or os.getcwd()
        self.trace_id: Optional[str] = None

    def set_step_result(self, name: str, result: StepResult) -> None:
        self.steps[name] = result

    def get_step_result(self, name: str) -> Optional[StepResult]:
        return self.steps.get(name)

    def set_shared(self, key: str, value: Any) -> None:
        self.shared_context[key] = value

    def get_shared(self, key: str) -> Any:
        return self.shared_context.get(key)

    def get_summary(self) -> Dict[str, Any]:
        total = len(self.steps)
        successful = sum(1 for r in self.steps.values() if r.success)
        failed = total - successful
        return {
            "total_steps": total,
            "successful_steps": successful,
            "failed_steps": failed,
        }


class TemplateResolver:
    """模板变量解析器

    支持:
      {{var}}              -> initial_args[var]
      {{steps.name.output}} -> steps[name].output
      {{steps.name.success}} -> steps[name].success
      {{shared_context.key}} -> shared_context[key]
      {{env.VAR}}          -> env[VAR]
    """

    STEP_REF_RE = re.compile(r"^steps\.([^.]+)\.(output|success)$")

    def __init__(self, context: Optional[ExecutionContext] = None):
        self._context = context

    def resolve(self, template: Any, context: Optional[ExecutionContext] = None) -> Any:
        ctx = context or self._context
        if ctx is None:
            raise ValueError("TemplateResolver requires an ExecutionContext")
        if isinstance(template, str):
            return self._resolve_string(template, ctx)
        elif isinstance(template, dict):
            return {k: self.resolve(v, ctx) for k, v in template.items()}
        elif isinstance(template, list):
            return [self.resolve(item, ctx) for item in template]
        return template

    def _resolve_string(self, template: str, context: ExecutionContext) -> Any:
        # Check if it's a pure variable reference
        pure_match = re.match(r"^\{\{(.+?)\}\}$", template.strip())
        if pure_match:
            var_path = pure_match.group(1).strip()
            value = self._get_value(var_path, context)
            if value is not None:
                return value
            return template  # Keep original if not found

        # String interpolation
        def replace_var(match: re.Match) -> str:
            var_path = match.group(1).strip()
            value = self._get_value(var_path, context)
            if value is None:
                return match.group(0)
            return str(value)

        return re.sub(r"\{\{(.+?)\}\}", replace_var, template)

    def _get_value(self, var_path: str, context: ExecutionContext) -> Any:
        # env.VAR
        if var_path.startswith("env."):
            env_var = var_path[4:]
            return context.env.get(env_var)

        # shared_context.key
        if var_path.startswith("shared_context."):
            key = var_path[15:]
            return context.get_shared(key)

        # steps.name.output or steps.name.success
        step_match = self.STEP_REF_RE.match(var_path)
        if step_match:
            step_name = step_match.group(1)
            attr = step_match.group(2)
            result = context.get_step_result(step_name)
            if result is None:
                return None
            if attr == "output":
                return result.output
            if attr == "success":
                return result.success

        # steps.name (return full result dict)
        if var_path.startswith("steps."):
            step_name = var_path[6:]
            result = context.get_step_result(step_name)
            if result:
                return result.to_dict()
            return None

        # initial_args / top-level variable
        if var_path in context.initial_args:
            return context.initial_args[var_path]
        if var_path in context.shared_context:
            return context.shared_context[var_path]

        return None


class ToolRegistry:
    """工具注册表"""

    def __init__(self, parent: Optional["ToolRegistry"] = None):
        self._tools: Dict[str, Callable] = {}
        self._parent = parent

    def register(self, name: str, handler: Callable) -> None:
        self._tools[name] = handler

    def get(self, name: str) -> Optional[Callable]:
        if name in self._tools:
            return self._tools[name]
        if self._parent:
            return self._parent.get(name)
        return None

    def list_tools(self) -> List[str]:
        tools = set(self._tools.keys())
        if self._parent:
            tools.update(self._parent.list_tools())
        return sorted(tools)

    def create_child(self) -> "ToolRegistry":
        return ToolRegistry(parent=self)


class PipelineEngine:
    """流水线执行引擎"""

    def __init__(
        self,
        tool_registry: Optional[ToolRegistry] = None,
        logger: Optional[Callable] = None,
    ):
        self.tool_registry = tool_registry or ToolRegistry()
        self.logger = logger or print
        self.template_resolver = TemplateResolver()

    async def execute(
        self,
        pipeline_config: Dict[str, Any],
        context: ExecutionContext,
        timeout_ms: Optional[int] = None,
        registry: Optional[ToolRegistry] = None,
    ) -> Dict[str, Any]:
        """执行完整流水线

        Args:
            pipeline_config: worker.yaml 解析后的配置
            context: 执行上下文
            timeout_ms: 总超时（毫秒）
            registry: 可选的工具注册表（覆盖默认）

        Returns:
            {"success": bool, "output": Any, "steps": list, "duration_ms": float}
        """
        tool_registry = registry or self.tool_registry
        pipeline = pipeline_config.get("pipeline", [])
        if not pipeline:
            return {"success": True, "output": None, "steps": [], "duration_ms": 0}

        start_time = time.time()
        steps_results: List[Dict[str, Any]] = []

        for step_config in pipeline:
            step_name = step_config.get("step", "unnamed")

            # 检查总超时
            if timeout_ms is not None:
                elapsed_ms = (time.time() - start_time) * 1000
                if elapsed_ms >= timeout_ms:
                    return {
                        "success": False,
                        "output": f"Pipeline timed out after {timeout_ms}ms",
                        "steps": steps_results,
                        "duration_ms": elapsed_ms,
                    }

            # 条件判断
            if "when" in step_config:
                condition = step_config["when"]
                if not self._evaluate_condition(condition, context):
                    self.logger(f"[Pipeline] Step '{step_name}' skipped (condition: {condition})")
                    continue

            # 执行步骤
            try:
                result = await self._execute_step(step_config, context, registry=tool_registry)
                context.set_step_result(step_name, result)
                steps_results.append(result.to_dict())

                if not result.success:
                    # 错误处理
                    should_continue = await self._handle_error(step_config, result, context)
                    if not should_continue:
                        duration = (time.time() - start_time) * 1000
                        return {
                            "success": False,
                            "output": result.error,
                            "steps": steps_results,
                            "duration_ms": duration,
                        }
            except Exception as e:
                error_result = StepResult(
                    step_name=step_name,
                    success=False,
                    error=str(e),
                )
                context.set_step_result(step_name, error_result)
                steps_results.append(error_result.to_dict())
                should_continue = await self._handle_error(step_config, error_result, context)
                if not should_continue:
                    duration = (time.time() - start_time) * 1000
                    return {
                        "success": False,
                        "output": str(e),
                        "steps": steps_results,
                        "duration_ms": duration,
                    }

        duration = (time.time() - start_time) * 1000
        # 获取最终结果
        final_output = self._get_final_output(pipeline_config, context)

        return {
            "success": True,
            "output": final_output,
            "steps": steps_results,
            "duration_ms": duration,
        }

    async def _execute_step(
        self,
        step_config: Dict[str, Any],
        context: ExecutionContext,
        registry: Optional[ToolRegistry] = None,
    ) -> StepResult:
        """执行单个步骤"""
        step_name = step_config.get("step", "unnamed")
        start_time = time.time()
        tool_registry = registry or self.tool_registry

        # 解析参数（模板变量替换）
        raw_args = step_config.get("args") or step_config.get("with", {})
        args = self.template_resolver.resolve(raw_args, context)

        # 确定工具
        tool_name = step_config.get("tool")
        invoke = step_config.get("invoke")
        invoke_parallel = step_config.get("invoke_parallel") or step_config.get("parallel")

        if invoke_parallel:
            # 并行执行
            output = await self._execute_parallel(invoke_parallel, args, context, tool_registry)
        elif invoke:
            # 子 agent 调用
            output = await self._execute_invoke(invoke, args, context)
        elif tool_name:
            # 工具调用
            handler = tool_registry.get(tool_name)
            if handler is None:
                return StepResult(
                    step_name=step_name,
                    success=False,
                    error=f"Tool '{tool_name}' not found",
                )
            output = await self._call_handler(handler, args, context)
        else:
            return StepResult(
                step_name=step_name,
                success=False,
                error="No tool or invoke specified",
            )

        # 结果映射
        output = self._apply_result_mapping(step_config, output, context)

        duration = (time.time() - start_time) * 1000
        return StepResult(
            step_name=step_name,
            success=True,
            output=output,
            duration_ms=duration,
        )

    async def _call_handler(
        self,
        handler: Callable,
        args: Any,
        context: ExecutionContext,
    ) -> Any:
        """调用工具处理器"""
        if asyncio.iscoroutinefunction(handler):
            return await handler(args, context)
        return handler(args, context)

    async def _execute_parallel(
        self,
        parallel_configs: List[Dict[str, Any]],
        args: Any,
        context: ExecutionContext,
        registry: Optional[ToolRegistry] = None,
    ) -> List[Any]:
        """并行执行多个子 agent"""
        tool_registry = registry or self.tool_registry
        tasks = []
        for config in parallel_configs:
            agent_name = config.get("agent")
            if agent_name:
                tasks.append(self._execute_invoke(agent_name, args, context, tool_registry))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [
            {"agent": parallel_configs[i].get("agent"), "result": r}
            if not isinstance(r, Exception)
            else {"agent": parallel_configs[i].get("agent"), "error": str(r)}
            for i, r in enumerate(results)
        ]

    async def _execute_invoke(
        self,
        agent_name: str,
        args: Any,
        context: ExecutionContext,
        registry: Optional[ToolRegistry] = None,
    ) -> Any:
        """调用子 agent"""
        # 简化实现：通过 invoke_agent 工具调用
        tool_registry = registry or self.tool_registry
        handler = tool_registry.get("invoke_agent")
        if handler:
            return await self._call_handler(handler, {"agent": agent_name, "input": args}, context)
        return {"agent": agent_name, "status": "invoked", "input": args}

    def _evaluate_condition(self, condition: str, context: ExecutionContext) -> bool:
        """评估条件表达式"""
        # 先解析模板变量
        resolved = self.template_resolver.resolve(condition, context)
        if isinstance(resolved, bool):
            return resolved
        if isinstance(resolved, str):
            # 尝试解析为比较表达式
            cmp_result = self._parse_comparison(resolved)
            if cmp_result is not None:
                return cmp_result
            # 简单布尔判断
            return resolved.lower() not in ("false", "0", "", "none", "null")
        return bool(resolved)

    def _parse_comparison(self, resolved: str) -> Optional[bool]:
        """解析比较表达式，如 'hello == world'"""
        import operator

        ops = {
            "==": operator.eq,
            "!=": operator.ne,
            ">=": operator.ge,
            "<=": operator.le,
            ">": operator.gt,
            "<": operator.lt,
        }

        for op_str, op_func in ops.items():
            if op_str in resolved:
                parts = resolved.split(op_str, 1)
                if len(parts) == 2:
                    left = parts[0].strip()
                    right = parts[1].strip()
                    # 尝试数值比较
                    try:
                        left_val = float(left)
                        right_val = float(right)
                        return op_func(left_val, right_val)
                    except ValueError:
                        pass
                    # 字符串比较
                    return op_func(left, right)
        return None

    async def _handle_error(
        self,
        step_config: Dict[str, Any],
        result: StepResult,
        context: ExecutionContext,
    ) -> bool:
        """处理步骤错误，返回是否继续执行"""
        on_fail = step_config.get("on_fail", "abort")
        step_name = step_config.get("step", "unnamed")

        if on_fail == "abort":
            self.logger(f"[Pipeline] Step '{step_name}' failed, aborting pipeline")
            return False

        if on_fail == "skip":
            self.logger(f"[Pipeline] Step '{step_name}' failed, skipping")
            return True

        if on_fail == "continue":
            self.logger(f"[Pipeline] Step '{step_name}' failed, continuing")
            return True

        # 重试策略
        if isinstance(on_fail, dict) and "retry" in on_fail:
            retry_config = on_fail["retry"]
            max_attempts = retry_config if isinstance(retry_config, int) else retry_config.get("max_attempts", 3)
            backoff = retry_config.get("backoff", "fixed") if isinstance(retry_config, dict) else "fixed"
            initial_delay = retry_config.get("initial_delay_ms", 1000) if isinstance(retry_config, dict) else 1000

            for attempt in range(1, max_attempts + 1):
                delay = self._compute_backoff(attempt, backoff, initial_delay)
                self.logger(f"[Pipeline] Retrying step '{step_name}' (attempt {attempt}/{max_attempts}, delay {delay}ms)")
                await asyncio.sleep(delay / 1000)

                try:
                    new_result = await self._execute_step(step_config, context)
                    if new_result.success:
                        context.set_step_result(step_name, new_result)
                        return True
                except Exception as e:
                    self.logger(f"[Pipeline] Retry {attempt} failed: {e}")

            self.logger(f"[Pipeline] Step '{step_name}' exhausted all retries")
            return False

        return False

    def _compute_backoff(
        self,
        attempt: int,
        backoff_type: str,
        initial_delay_ms: float,
    ) -> float:
        """计算退避延迟"""
        if backoff_type == "exponential":
            return initial_delay_ms * (2 ** (attempt - 1))
        return initial_delay_ms

    def _apply_result_mapping(
        self,
        step_config: Dict[str, Any],
        output: Any,
        context: ExecutionContext,
    ) -> Any:
        """应用结果映射"""
        # output / result 变量映射
        output_var = step_config.get("output") or step_config.get("result")
        if output_var:
            context.set_shared(output_var, output)

        # as 映射（从输出中提取字段）
        as_mapping = step_config.get("as")
        if as_mapping and isinstance(output, dict):
            for key, src_field in as_mapping.items():
                if isinstance(src_field, str) and src_field in output:
                    context.set_shared(key, output[src_field])

        return output

    def _get_final_output(
        self,
        pipeline_config: Dict[str, Any],
        context: ExecutionContext,
    ) -> Any:
        """获取流水线最终结果"""
        # 如果有 output 配置，解析它
        output_config = pipeline_config.get("output")
        if output_config:
            return self.template_resolver.resolve(output_config, context)

        # 返回最后一步的输出
        pipeline = pipeline_config.get("pipeline", [])
        if pipeline:
            last_step = pipeline[-1].get("step", "")
            result = context.get_step_result(last_step)
            if result:
                return result.output

        return None

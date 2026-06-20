import importlib
from typing import Any, Dict, List, Optional


class WorkflowLoader:
    """将 YAML Workflow 配置构建为 Workflow 配置对象。

    支持的 step 类型：
    - agent      - Agent 执行步骤
    - team       - Team 执行步骤
    - function   - 函数调用步骤
    - condition  - 条件分支步骤
    - parallel   - 并行执行步骤
    - loop       - 循环步骤
    - router     - 路由步骤（已包含在 condition 中处理）
    """

    def __init__(self, agent_loader=None, team_loader=None, config_resolver=None):
        self.agent_loader = agent_loader
        self.team_loader = team_loader
        self.config_resolver = config_resolver
        self._cache: Dict[str, Dict[str, Any]] = {}

    def load(self, name: str, workflow_config: Dict[str, Any], base_dir: str = "") -> Dict[str, Any]:
        cache_key = f"{name}::{base_dir}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        resolved = self.config_resolver.resolve(workflow_config, base_dir) if self.config_resolver else workflow_config

        steps = resolved.get("steps", [])
        built_steps = []
        for step in steps:
            if isinstance(step, dict):
                built_steps.append(self._build_step(step, base_dir))

        result = {
            "name": name,
            "description": resolved.get("description", ""),
            "entry_task": resolved.get("entry_task", ""),
            "steps": built_steps,
            "inputs": resolved.get("inputs", []),
            "outputs": resolved.get("outputs", []),
        }

        self._cache[cache_key] = result
        return result

    def _build_step(self, step: Dict[str, Any], base_dir: str) -> Dict[str, Any]:
        step_type = step.get("type", "agent")
        common = {
            "name": step.get("name", ""),
            "type": step_type,
            "description": step.get("description", ""),
        }

        if step_type == "agent":
            common.update(self._build_agent_step(step, base_dir))
        elif step_type == "team":
            common.update(self._build_team_step(step, base_dir))
        elif step_type == "function":
            common.update(self._build_function_step(step))
        elif step_type == "condition":
            common.update(self._build_condition(step, base_dir))
        elif step_type == "parallel":
            common.update(self._build_parallel(step, base_dir))
        elif step_type == "loop":
            common.update(self._build_loop(step, base_dir))
        else:
            common["config"] = {k: v for k, v in step.items() if k not in {"name", "type", "description"}}

        return common

    def _build_agent_step(self, step: Dict[str, Any], base_dir: str) -> Dict[str, Any]:
        agent_ref = step.get("agent", step.get("agent_ref", ""))
        agent_config = step.get("agent_config") or {}
        extra = {}
        if self.agent_loader is not None and agent_ref:
            if agent_config:
                extra["agent"] = self.agent_loader.load(agent_ref, agent_config, base_dir)
            else:
                extra["agent"] = {"name": agent_ref}
        else:
            extra["agent_ref"] = agent_ref
        extra["input"] = step.get("input", "")
        extra["output_key"] = step.get("output_key", step.get("name", ""))
        return extra

    def _build_team_step(self, step: Dict[str, Any], base_dir: str) -> Dict[str, Any]:
        team_ref = step.get("team", step.get("team_ref", ""))
        extra = {}
        if self.team_loader is not None and team_ref:
            extra["team"] = {"name": team_ref}
        else:
            extra["team_ref"] = team_ref
        extra["input"] = step.get("input", "")
        extra["output_key"] = step.get("output_key", step.get("name", ""))
        return extra

    def _build_function_step(self, step: Dict[str, Any]) -> Dict[str, Any]:
        func_ref = step.get("function", step.get("function_ref", ""))
        resolved = self._resolve_function(func_ref)
        extra = {
            "function_ref": func_ref,
            "function": resolved if resolved else func_ref,
            "args": step.get("args", {}),
            "output_key": step.get("output_key", step.get("name", "")),
        }
        return extra

    def _build_condition(self, step: Dict[str, Any], base_dir: str) -> Dict[str, Any]:
        evaluator_ref = step.get("evaluator", step.get("evaluator_ref", ""))
        cases = step.get("cases", {}) or {}
        built_cases = {}
        for case_label, case_steps in cases.items():
            if isinstance(case_steps, list):
                built_cases[case_label] = [
                    self._build_step(s, base_dir) for s in case_steps if isinstance(s, dict)
                ]
        default_case = step.get("default", []) or []
        built_default = [
            self._build_step(s, base_dir) for s in default_case if isinstance(s, dict)
        ]
        extra = {
            "evaluator_ref": evaluator_ref,
            "evaluator": self._resolve_function(evaluator_ref),
            "cases": built_cases,
            "default": built_default,
            "output_key": step.get("output_key", step.get("name", "")),
        }
        return extra

    def _build_parallel(self, step: Dict[str, Any], base_dir: str) -> Dict[str, Any]:
        branches = step.get("branches", []) or []
        built_branches = []
        for branch in branches:
            if isinstance(branch, dict):
                branch_steps = branch.get("steps", []) or []
                built_steps = [
                    self._build_step(s, base_dir) for s in branch_steps if isinstance(s, dict)
                ]
                built_branches.append({
                    "name": branch.get("name", ""),
                    "steps": built_steps,
                })
        extra = {
            "branches": built_branches,
            "output_key": step.get("output_key", step.get("name", "")),
        }
        return extra

    def _build_loop(self, step: Dict[str, Any], base_dir: str) -> Dict[str, Any]:
        evaluator_ref = step.get("evaluator", step.get("evaluator_ref", ""))
        loop_steps = step.get("steps", []) or []
        built_steps = [
            self._build_step(s, base_dir) for s in loop_steps if isinstance(s, dict)
        ]
        extra = {
            "evaluator_ref": evaluator_ref,
            "evaluator": self._resolve_function(evaluator_ref),
            "max_iterations": step.get("max_iterations", 5),
            "steps": built_steps,
            "output_key": step.get("output_key", step.get("name", "")),
        }
        return extra

    def _resolve_function(self, func_ref: str) -> Optional[Any]:
        """解析函数引用，格式: 'module.path:func_name' 或 'module.path.func_name'。

        返回可调用对象，或者 None（如果无法解析）。
        """
        if not func_ref or not isinstance(func_ref, str):
            return None
        if ":" in func_ref:
            module_path, func_name = func_ref.split(":", 1)
        elif "." in func_ref:
            parts = func_ref.rsplit(".", 1)
            module_path, func_name = parts[0], parts[1]
        else:
            return None

        try:
            module = importlib.import_module(module_path)
            return getattr(module, func_name, None)
        except (ImportError, AttributeError):
            return None

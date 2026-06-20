from typing import Any, Dict, List, Optional


TOOL_REGISTRY = {
    "web_search": {"name": "WebSearch", "params": ["provider"]},
    "duck_duck_go": {"name": "DuckDuckGo", "params": []},
    "calculator": {"name": "Calculator", "params": []},
    "shell": {"name": "ShellTool", "params": ["run_dir", "description"]},
    "file_writer": {"name": "FileWriter", "params": []},
    "file_reader": {"name": "FileReader", "params": []},
}


MODEL_REGISTRY = {
    "openai": {"module": "agno.models.openai", "class": "OpenAIChat"},
    "anthropic": {"module": "agno.models.anthropic", "class": "Claude"},
    "ollama": {"module": "agno.models.ollama", "class": "Ollama"},
    "groq": {"module": "agno.models.groq", "class": "Groq"},
    "deepseek": {"module": "agno.models.deepseek", "class": "DeepSeekChat"},
    "xai": {"module": "agno.models.xai", "class": "xAI"},
    "google": {"module": "agno.models.google", "class": "Gemini"},
    "nvidia": {"module": "agno.models.nvidia", "class": "Nvidia"},
}


class AgentLoader:
    """将 YAML Agent 配置构建为可运行的配置对象。

    在不依赖 Agno 框架的前提下，构建与 Agno 兼容的配置字典，
    下游可以直接映射为 agno.agent.Agent 对象。
    """

    def __init__(self, definition_loader=None, config_resolver=None, mcp_builder=None):
        self.definition_loader = definition_loader
        self.config_resolver = config_resolver
        self.mcp_builder = mcp_builder
        self._cache: Dict[str, Dict[str, Any]] = {}

    def load(self, name: str, agent_config: Dict[str, Any], base_dir: str = "") -> Dict[str, Any]:
        cache_key = f"{name}::{base_dir}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        resolved = self.config_resolver.resolve(agent_config, base_dir) if self.config_resolver else agent_config

        result = {
            "name": name,
            "role": resolved.get("role", ""),
            "description": resolved.get("description", ""),
            "instructions": self._ensure_list(resolved.get("instructions", [])),
            "tools": self._build_all_tools(resolved, base_dir),
            "model": self._build_model(resolved.get("model", {})),
            "memory": self._build_memory(resolved.get("memory")),
            "knowledge": self._build_knowledge(resolved.get("knowledge")),
            "markdown": bool(resolved.get("markdown", True)),
            "add_datetime_to_instructions": bool(resolved.get("add_datetime_to_instructions", False)),
            "debug_mode": bool(resolved.get("debug_mode", False)),
        }

        self._cache[cache_key] = result
        return result

    def _ensure_list(self, value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v) for v in value]
        return [str(value)]

    def _build_model(self, model_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not model_config or not isinstance(model_config, dict):
            return {}
        provider = model_config.get("provider", "")
        entry = MODEL_REGISTRY.get(provider, {})
        return {
            "provider": provider,
            "id": model_config.get("id", ""),
            "api_key": model_config.get("api_key", ""),
            "module": entry.get("module", ""),
            "class_name": entry.get("class", ""),
            "params": {k: v for k, v in model_config.items() if k not in {"provider", "id", "api_key"}},
        }

    def _build_tools(self, tool_configs: List[Any], base_dir: str) -> List[Dict[str, Any]]:
        result = []
        if not tool_configs or not isinstance(tool_configs, list):
            return result

        for tool in tool_configs:
            if isinstance(tool, dict) and "name" in tool:
                name = tool["name"]
                entry = TOOL_REGISTRY.get(name, {})
                params = {k: v for k, v in tool.items() if k != "name"}
                result.append({
                    "name": entry.get("name", name),
                    "type": "builtin",
                    "params": params,
                })
            elif isinstance(tool, str):
                entry = TOOL_REGISTRY.get(tool, {})
                result.append({
                    "name": entry.get("name", tool),
                    "type": "builtin",
                    "params": {},
                })
        return result

    def _build_skill_tools(self, skill_names: List[str]) -> List[Dict[str, Any]]:
        result = []
        if not skill_names or not isinstance(skill_names, list) or self.definition_loader is None:
            return result

        for skill_name in skill_names:
            skill_def = self.definition_loader.get_skill(skill_name)
            if skill_def and isinstance(skill_def, dict):
                result.append({
                    "name": skill_name,
                    "type": "skill",
                    "params": {k: v for k, v in skill_def.items() if k != "name"},
                })
        return result

    def _build_mcp_tools(self, mcp_names: List[str]) -> List[Dict[str, Any]]:
        result = []
        if not mcp_names or not isinstance(mcp_names, list):
            return result

        for mcp_name in mcp_names:
            if self.definition_loader is not None:
                mcp_def = self.definition_loader.get_mcp(mcp_name)
            else:
                mcp_def = None
            if mcp_def and self.mcp_builder is not None:
                built = self.mcp_builder.build(mcp_def)
                result.append({
                    "name": mcp_name,
                    "type": "mcp",
                    "config": built,
                })
        return result

    def _build_all_tools(self, resolved: Dict[str, Any], base_dir: str) -> List[Dict[str, Any]]:
        all_tools: List[Dict[str, Any]] = []
        tools_section = resolved.get("tools")
        if isinstance(tools_section, dict):
            all_tools.extend(self._build_tools(tools_section.get("builtin", []), base_dir))
            all_tools.extend(self._build_skill_tools(tools_section.get("skills", [])))
            all_tools.extend(self._build_mcp_tools(tools_section.get("mcps", [])))
            all_tools.extend(self._build_tools(tools_section.get("extra", []), base_dir))
        else:
            all_tools.extend(self._build_tools(tools_section, base_dir) if tools_section else [])
        return all_tools

    def _build_memory(self, memory_config: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not memory_config or not isinstance(memory_config, dict):
            return None
        return {
            "type": memory_config.get("type", "short_term"),
            "table_name": memory_config.get("table_name", "agent_memory"),
        }

    def _build_knowledge(self, knowledge_config: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not knowledge_config or not isinstance(knowledge_config, dict):
            return None
        sources = knowledge_config.get("sources", [])
        if isinstance(sources, list):
            return {
                "type": knowledge_config.get("type", "pdf_url"),
                "sources": sources,
            }
        return None

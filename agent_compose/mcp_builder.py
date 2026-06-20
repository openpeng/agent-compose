import os
from typing import Any, Dict, List


class MCPBuilder:
    """构建 MCP 工具配置。

    支持的 MCP 类型：
    - stdio      — 标准 I/O 类型 MCP
    - sse        — SSE 类型 MCP
    - multi      — 多 MCP 组合（代理多个 MCP Server）

    返回格式与 Agno MCP 框架兼容。
    """

    VALID_TYPES = {"stdio", "sse", "multi"}

    def build(self, mcp_config: Dict[str, Any]) -> Dict[str, Any]:
        mcp_type = mcp_config.get("type", "stdio")
        if mcp_type not in self.VALID_TYPES:
            raise ValueError(f"Unsupported MCP type: {mcp_type}. Use one of {self.VALID_TYPES}")

        if mcp_type == "stdio":
            return self._build_stdio(mcp_config)
        if mcp_type == "sse":
            return self._build_sse(mcp_config)
        if mcp_type == "multi":
            return self._build_multi(mcp_config)
        return mcp_config

    def _build_stdio(self, config: Dict[str, Any]) -> Dict[str, Any]:
        args = config.get("args", [])
        if isinstance(args, str):
            args = [args]

        env_vars = config.get("env", {})
        if not isinstance(env_vars, dict):
            env_vars = {}

        return {
            "type": "stdio",
            "name": config.get("name", ""),
            "command": config.get("command", ""),
            "args": args,
            "env": env_vars,
        }

    def _build_sse(self, config: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "type": "sse",
            "name": config.get("name", ""),
            "url": config.get("url", ""),
            "auth_token": config.get("auth_token", ""),
        }

    def _build_multi(self, config: Dict[str, Any]) -> Dict[str, Any]:
        servers = config.get("servers", [])
        if not isinstance(servers, list):
            servers = []

        built_servers = []
        for server in servers:
            if isinstance(server, dict):
                server_type = server.get("type", "stdio")
                if server_type == "stdio":
                    built_servers.append(self._build_stdio(server))
                elif server_type == "sse":
                    built_servers.append(self._build_sse(server))
                else:
                    built_servers.append(server)

        return {
            "type": "multi",
            "name": config.get("name", ""),
            "servers": built_servers,
        }

    def build_all(self, mcp_configs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        result = []
        for cfg in mcp_configs:
            if isinstance(cfg, dict):
                result.append(self.build(cfg))
        return result

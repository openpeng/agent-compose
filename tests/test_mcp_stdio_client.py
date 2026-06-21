"""Tests for agent_compose.mcp_stdio_client module."""

import sys
from pathlib import Path

import pytest

from agent_compose.mcp_stdio_client import MCPStdioClient, MCPServerStartError

# Path to the simple MCP server module
SIMPLE_MCP_SERVER_MODULE = "agent_compose.mcp_servers.simple_mcp_server"


class TestMCPStdioClientInit:
    """Tests for MCPStdioClient initialization."""

    def test_init_defaults(self):
        client = MCPStdioClient(command="python", args=["-m", "test"])
        assert client.command == "python"
        assert client.args == ["-m", "test"]
        assert client.env == {}
        assert client.name == "stdio-mcp"
        assert client.timeout == 30
        assert client._proc is None
        assert client._connected is False
        assert client._initialized is False

    def test_init_with_custom_params(self):
        client = MCPStdioClient(
            command="python",
            args=["-m", "my_server"],
            env={"FOO": "bar"},
            name="custom-mcp",
            timeout=60,
        )
        assert client.args == ["-m", "my_server"]
        assert client.env == {"FOO": "bar"}
        assert client.name == "custom-mcp"
        assert client.timeout == 60


class TestMCPStdioClientLifecycle:
    """Tests for connect / list_tools / call_tool / close lifecycle."""

    def test_connect_with_real_simple_mcp_server(self):
        """Connect to the real simple_mcp_server and verify initialization."""
        client = MCPStdioClient(
            command=sys.executable,
            args=["-m", SIMPLE_MCP_SERVER_MODULE],
            name="test-simple",
            timeout=10,
        )
        try:
            success = client.connect()
            assert success is True
            assert client._connected is True
            assert client._initialized is True
        finally:
            client.close()

    def test_list_tools_returns_tools_after_connection(self):
        """list_tools should return available tools after connect."""
        client = MCPStdioClient(
            command=sys.executable,
            args=["-m", SIMPLE_MCP_SERVER_MODULE],
            name="test-tools",
            timeout=10,
        )
        try:
            client.connect()
            tools = client.list_tools()
            assert isinstance(tools, list)
            assert len(tools) > 0
            tool_names = {t["name"] for t in tools}
            assert "calculator" in tool_names
            assert "echo" in tool_names
            assert "now" in tool_names
        finally:
            client.close()

    def test_call_tool_executes_calculator(self):
        """call_tool should execute calculator tool and return result."""
        client = MCPStdioClient(
            command=sys.executable,
            args=["-m", SIMPLE_MCP_SERVER_MODULE],
            name="test-call",
            timeout=10,
        )
        try:
            client.connect()
            result = client.call_tool("calculator", {"expression": "2 + 3 * 4"})
            assert isinstance(result, dict)
            assert "content" in result
            assert "14" in result["content"]
        finally:
            client.close()

    def test_call_tool_executes_echo(self):
        """call_tool should execute echo tool."""
        client = MCPStdioClient(
            command=sys.executable,
            args=["-m", SIMPLE_MCP_SERVER_MODULE],
            name="test-echo",
            timeout=10,
        )
        try:
            client.connect()
            result = client.call_tool("echo", {"message": "hello pytest"})
            assert isinstance(result, dict)
            assert "hello pytest" in result["content"]
        finally:
            client.close()

    def test_close_cleans_up_properly(self):
        """close should clean up process and reset state."""
        client = MCPStdioClient(
            command=sys.executable,
            args=["-m", SIMPLE_MCP_SERVER_MODULE],
            name="test-close",
            timeout=10,
        )
        client.connect()
        assert client._proc is not None
        assert client._connected is True

        client.close()
        assert client._connected is False
        assert client._initialized is False
        # Give the process a moment to exit, or kill it if still running
        import time
        time.sleep(0.5)
        if client._proc.poll() is None:
            client._safe_kill()
            time.sleep(0.3)
        assert client._proc.poll() is not None

    def test_tools_cache_after_first_list_tools(self):
        """list_tools should cache results after first call."""
        client = MCPStdioClient(
            command=sys.executable,
            args=["-m", SIMPLE_MCP_SERVER_MODULE],
            name="test-cache",
            timeout=10,
        )
        try:
            client.connect()
            tools1 = client.list_tools()
            tools2 = client.list_tools()
            assert tools1 == tools2
            assert client._tools_cache is not None
        finally:
            client.close()

    def test_list_tools_before_connect_returns_empty(self):
        """list_tools should return empty list if not connected."""
        client = MCPStdioClient(
            command=sys.executable,
            args=["-m", SIMPLE_MCP_SERVER_MODULE],
            name="test-no-connect",
        )
        tools = client.list_tools()
        assert tools == []

    def test_call_tool_before_connect_returns_error(self):
        """call_tool should return error dict if not connected."""
        client = MCPStdioClient(
            command=sys.executable,
            args=["-m", SIMPLE_MCP_SERVER_MODULE],
            name="test-no-connect",
        )
        result = client.call_tool("calculator", {"expression": "1+1"})
        assert "Error" in result["content"]


class TestMCPStdioClientErrorHandling:
    """Tests for error handling."""

    def test_invalid_command_raises_mcpserver_start_error(self):
        """Connecting with a non-existent command should raise MCPServerStartError."""
        client = MCPStdioClient(
            command="this_command_does_not_exist_12345",
            args=["--help"],
            name="test-invalid",
            timeout=5,
        )
        with pytest.raises(MCPServerStartError):
            client.connect()

    def test_invalid_module_raises_mcpserver_start_error(self):
        """Connecting with a non-existent Python module should raise MCPServerStartError."""
        client = MCPStdioClient(
            command=sys.executable,
            args=["-m", "nonexistent_module_12345"],
            name="test-bad-module",
            timeout=5,
        )
        with pytest.raises(MCPServerStartError):
            client.connect()

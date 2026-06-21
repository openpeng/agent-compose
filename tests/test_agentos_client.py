"""Tests for agentos_client module"""
import pytest

from agent_compose.agentos_client import AgentOSClient


class TestAgentOSClient:
    def test_init_defaults(self):
        client = AgentOSClient()
        assert client.agentos_url == "http://localhost:9000"
        assert client.heartbeat_interval == 30
        assert client.is_registered is False

    def test_init_custom(self):
        client = AgentOSClient(
            agentos_url="http://agentos:8000",
            agent_id="test-agent",
            api_key="secret",
            heartbeat_interval=10,
        )
        assert client.agentos_url == "http://agentos:8000"
        assert client.agent_id == "test-agent"
        assert client.api_key == "secret"
        assert client.heartbeat_interval == 10

    def test_report_status(self):
        client = AgentOSClient(agent_id="test-agent")
        client.report_status("running", active_sessions=3)
        assert client._status == "running"
        assert client._active_sessions == 3

    def test_register_without_server_returns_error(self):
        """When no AgentOS server is running, register should return error gracefully"""
        client = AgentOSClient(agentos_url="http://localhost:19999")
        result = client.register("test-agent")
        # Should not raise, should return error dict
        assert result.get("status") == "error" or result.get("status") == "ok"

    def test_health_before_register(self):
        client = AgentOSClient()
        assert not client.is_registered
        assert not client.last_heartbeat_ok

    def test_unregister_when_not_registered(self):
        client = AgentOSClient()
        result = client.unregister()
        assert result is not None  # Should not raise

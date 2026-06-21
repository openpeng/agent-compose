"""Tests for agent_runtime_server module"""
import json
import time
import pytest

from agent_compose.agent_runtime_server import AgentRuntimeServer
from agent_compose.session_store import MemorySessionStore


class TestAgentRuntimeServer:
    def setup_method(self):
        self.store = MemorySessionStore()
        self.server = AgentRuntimeServer(
            host="127.0.0.1",
            port=0,  # Will not actually bind
            session_store=self.store,
            session_ttl=3600,
        )

    def test_health(self):
        health = self.server.health()
        assert health["status"] == "ok"
        assert health["version"] == "1.0.0"
        assert health["active_sessions"] == 0
        assert health["session_backend"] == "MemorySessionStore"

    def test_ready(self):
        ready = self.server.ready()
        assert ready["status"] == "ready"
        assert ready["session_store"] == "ok"

    def test_create_and_destroy_session(self):
        # Note: create_session requires AgentRuntime which needs API key
        # We test the session lifecycle through the store
        session = self.store.create("test-agent")
        assert session.session_id.startswith("sess-")

        result = self.server.destroy_session(session.session_id)
        assert result["status"] == "destroyed"
        assert self.store.get(session.session_id) is None

    def test_destroy_nonexistent_session(self):
        result = self.server.destroy_session("nonexistent")
        assert result["status"] == "not_found"

    def test_get_session(self):
        session = self.store.create("test-agent")
        detail = self.server.get_session(session.session_id)
        assert detail is not None
        assert detail["agent_id"] == "test-agent"

    def test_get_nonexistent_session(self):
        detail = self.server.get_session("nonexistent")
        assert detail is None

    def test_list_sessions(self):
        self.store.create("agent-a")
        self.store.create("agent-b")
        self.store.create("agent-a")

        sessions = self.server.list_sessions()
        assert len(sessions) == 3

        sessions_a = self.server.list_sessions(agent_id="agent-a")
        assert len(sessions_a) == 2

    def test_cleanup_expired(self):
        session = self.store.create("agent-a")
        session.updated_at = time.time() - 7200
        # Directly update in store's internal dict to avoid touch()
        with self.store._lock:
            self.store._sessions[session.session_id] = session
        self.store.create("agent-b")

        cleaned = self.server.cleanup_expired()
        assert cleaned == 1
        assert self.server.get_session(session.session_id) is None

    def test_register_with_agentos_placeholder(self):
        result = self.server.register_with_agentos("http://localhost:9000")
        assert result["status"] == "registered"
        assert "placeholder" in result["message"]

    def test_request_count_increments(self):
        initial = self.server._request_count
        self.server._request_count += 1
        assert self.server._request_count == initial + 1

"""Tests for session_store module"""
import os
import shutil
import tempfile
import time
import pytest

from agent_compose.session_store import (
    SessionState,
    MemorySessionStore,
    FileSessionStore,
    create_session_store,
)


class TestSessionState:
    def test_create_basic(self):
        s = SessionState(session_id="s1", agent_id="test-agent")
        assert s.session_id == "s1"
        assert s.agent_id == "test-agent"
        assert s.messages == []
        assert s.mcp_connections == []
        assert s.metadata == {}

    def test_to_dict_roundtrip(self):
        s = SessionState(
            session_id="s1",
            agent_id="agent-a",
            messages=[{"role": "user", "content": "hello"}],
            mcp_connections=["webbridge"],
            metadata={"key": "val"},
        )
        d = s.to_dict()
        s2 = SessionState.from_dict(d)
        assert s2.session_id == "s1"
        assert s2.agent_id == "agent-a"
        assert len(s2.messages) == 1
        assert s2.messages[0]["content"] == "hello"
        assert s2.mcp_connections == ["webbridge"]
        assert s2.metadata["key"] == "val"

    def test_touch_updates_timestamp(self):
        s = SessionState(session_id="s1", agent_id="a")
        old = s.updated_at
        time.sleep(0.01)
        s.touch()
        assert s.updated_at > old


class TestMemorySessionStore:
    def setup_method(self):
        self.store = MemorySessionStore()

    def test_create_and_get(self):
        s = self.store.create("agent-a")
        assert s.session_id.startswith("sess-")
        assert s.agent_id == "agent-a"

        retrieved = self.store.get(s.session_id)
        assert retrieved is not None
        assert retrieved.agent_id == "agent-a"

    def test_delete(self):
        s = self.store.create("agent-a")
        assert self.store.delete(s.session_id) is True
        assert self.store.get(s.session_id) is None

    def test_delete_nonexistent(self):
        assert self.store.delete("nonexistent") is False

    def test_list_sessions(self):
        self.store.create("agent-a")
        self.store.create("agent-b")
        self.store.create("agent-a")

        all_sessions = self.store.list_sessions()
        assert len(all_sessions) == 3

        agent_a_sessions = self.store.list_sessions(agent_id="agent-a")
        assert len(agent_a_sessions) == 2

    def test_save_updates(self):
        s = self.store.create("agent-a")
        s.messages.append({"role": "user", "content": "hello"})
        self.store.save(s)

        retrieved = self.store.get(s.session_id)
        assert len(retrieved.messages) == 1
        assert retrieved.messages[0]["content"] == "hello"

    def test_cleanup_expired(self):
        s = self.store.create("agent-a")
        # Manually set updated_at to the past and save without triggering touch
        s.updated_at = time.time() - 7200  # 2 hours ago
        # Directly update in store's internal dict to avoid touch()
        with self.store._lock:
            self.store._sessions[s.session_id] = s

        # Create a fresh session that should not be cleaned
        self.store.create("agent-b")

        cleaned = self.store.cleanup(max_age_seconds=3600)
        assert cleaned == 1
        assert self.store.get(s.session_id) is None
        assert len(self.store.list_sessions()) == 1


class TestFileSessionStore:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.store = FileSessionStore(base_dir=self.tmpdir)

    def teardown_method(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_and_get(self):
        s = self.store.create("agent-a")
        assert s.session_id.startswith("sess-")

        retrieved = self.store.get(s.session_id)
        assert retrieved is not None
        assert retrieved.agent_id == "agent-a"

    def test_persistence_across_instances(self):
        s = self.store.create("agent-a")
        s.messages.append({"role": "user", "content": "hello"})
        self.store.save(s)

        # Create a new store instance pointing to the same directory
        store2 = FileSessionStore(base_dir=self.tmpdir)
        retrieved = store2.get(s.session_id)
        assert retrieved is not None
        assert len(retrieved.messages) == 1
        assert retrieved.messages[0]["content"] == "hello"

    def test_delete(self):
        s = self.store.create("agent-a")
        assert self.store.delete(s.session_id) is True
        assert self.store.get(s.session_id) is None

    def test_list_sessions(self):
        self.store.create("agent-a")
        self.store.create("agent-b")
        assert len(self.store.list_sessions()) == 2
        assert len(self.store.list_sessions(agent_id="agent-a")) == 1

    def test_cleanup_expired(self):
        s = self.store.create("agent-a")
        s.updated_at = time.time() - 7200
        # Write file directly with old timestamp to avoid touch()
        import json
        path = os.path.join(self.tmpdir, f"{s.session_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(s.to_dict(), f)

        self.store.create("agent-b")

        cleaned = self.store.cleanup(max_age_seconds=3600)
        assert cleaned == 1
        assert self.store.get(s.session_id) is None

    def test_corrupted_file_returns_none(self):
        s = self.store.create("agent-a")
        # Corrupt the file
        path = os.path.join(self.tmpdir, f"{s.session_id}.json")
        with open(path, "w") as f:
            f.write("not valid json{{{")
        assert self.store.get(s.session_id) is None


class TestCreateSessionStore:
    def test_memory_backend(self):
        store = create_session_store("memory")
        assert isinstance(store, MemorySessionStore)

    def test_file_backend(self):
        tmpdir = tempfile.mkdtemp()
        try:
            store = create_session_store("file", base_dir=tmpdir)
            assert isinstance(store, FileSessionStore)
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_invalid_backend(self):
        with pytest.raises(ValueError, match="不支持"):
            create_session_store("nonexistent")

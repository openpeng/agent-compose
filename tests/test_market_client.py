"""
Market Client 单元测试
"""

import json
import os
import tempfile

import pytest

from agent_compose.market_client import (
    MarketClient,
    AgentInfo,
    VersionInfo,
    VersionDiff,
)


class TestMarketClientUtils:
    def test_bump_version_patch(self):
        client = MarketClient()
        assert client._bump_version("1.0.0", "patch") == "1.0.1"
        assert client._bump_version("1.2.3", "patch") == "1.2.4"

    def test_bump_version_minor(self):
        client = MarketClient()
        assert client._bump_version("1.0.0", "minor") == "1.1.0"
        assert client._bump_version("1.2.3", "minor") == "1.3.0"

    def test_bump_version_major(self):
        client = MarketClient()
        assert client._bump_version("1.0.0", "major") == "2.0.0"
        assert client._bump_version("1.2.3", "major") == "2.0.0"

    def test_bump_version_short(self):
        client = MarketClient()
        assert client._bump_version("1.0", "patch") == "1.0.1"
        assert client._bump_version("1", "minor") == "1.1.0"

    def test_cache_key(self):
        client = MarketClient()
        key1 = client._cache_key("agent-1", "1.0.0")
        key2 = client._cache_key("agent-1", "1.0.0")
        key3 = client._cache_key("agent-2", "1.0.0")
        assert key1 == key2
        assert key1 != key3

    def test_save_and_get_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = MarketClient(cache_dir=tmpdir)
            agent_dir = os.path.join(tmpdir, "agent")
            os.makedirs(agent_dir)
            with open(os.path.join(agent_dir, "agent.json"), "w") as f:
                json.dump({"identity": {"name": "test-agent", "version": "1.0.0"}}, f)

            client._save_to_cache("test-agent", agent_dir, "1.0.0")
            cached = client._get_from_cache("test-agent", "1.0.0")
            assert cached is not None
            assert os.path.exists(cached)

    def test_list_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = os.path.join(tmpdir, "cache")
            agent_dir = os.path.join(tmpdir, "agent")
            os.makedirs(agent_dir)
            with open(os.path.join(agent_dir, "agent.json"), "w") as f:
                json.dump({"identity": {"name": "cached-agent", "version": "2.0.0"}}, f)

            client = MarketClient(cache_dir=cache_dir)
            client._save_to_cache("cached-agent", agent_dir, "2.0.0")
            cache_list = client.list_cache()
            assert len(cache_list) == 1
            assert cache_list[0]["agent_id"] == "cached-agent"
            assert cache_list[0]["version"] == "2.0.0"

    def test_clear_cache(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_dir = os.path.join(tmpdir, "cache")
            agent_dir = os.path.join(tmpdir, "agent")
            os.makedirs(agent_dir)
            with open(os.path.join(agent_dir, "agent.json"), "w") as f:
                json.dump({"identity": {"name": "test", "version": "1.0.0"}}, f)

            client = MarketClient(cache_dir=cache_dir)
            client._save_to_cache("test", agent_dir, "1.0.0")
            client.clear_cache()
            assert client.list_cache() == []

    def test_pack_and_extract(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            source_dir = os.path.join(tmpdir, "source")
            os.makedirs(source_dir)
            with open(os.path.join(source_dir, "file.txt"), "w") as f:
                f.write("hello")

            client = MarketClient()
            package = client._pack_directory(source_dir, "test-agent", "1.0.0")
            assert os.path.exists(package)

            extract_dir = os.path.join(tmpdir, "extracted")
            client._extract_package(package, extract_dir)
            assert os.path.exists(os.path.join(extract_dir, "file.txt"))

            os.unlink(package)

    def test_diff_directories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dir_a = os.path.join(tmpdir, "a")
            dir_b = os.path.join(tmpdir, "b")
            os.makedirs(dir_a)
            os.makedirs(dir_b)

            # 共同文件
            with open(os.path.join(dir_a, "common.txt"), "w") as f:
                f.write("same")
            with open(os.path.join(dir_b, "common.txt"), "w") as f:
                f.write("same")

            # 修改文件
            with open(os.path.join(dir_a, "modified.txt"), "w") as f:
                f.write("old")
            with open(os.path.join(dir_b, "modified.txt"), "w") as f:
                f.write("new")

            # A 独有
            with open(os.path.join(dir_a, "removed.txt"), "w") as f:
                f.write("gone")

            # B 独有
            with open(os.path.join(dir_b, "added.txt"), "w") as f:
                f.write("new file")

            # agent.json
            for d, version in [(dir_a, "1.0.0"), (dir_b, "1.1.0")]:
                with open(os.path.join(d, "agent.json"), "w") as f:
                    json.dump({
                        "identity": {"name": "test", "version": version, "description": f"v{version}"},
                        "instructions": {"content": f"instructions {version}"},
                    }, f)

            client = MarketClient()
            diff = client._diff_directories(dir_a, dir_b, "1.0.0", "1.1.0")

            assert "added.txt" in diff.added_files
            assert "removed.txt" in diff.removed_files
            assert "modified.txt" in diff.modified_files
            assert "common.txt" in diff.unchanged_files
            assert "version" in diff.identity_changes
            assert "content" in diff.instruction_changes

    def test_update_changelog_new(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = MarketClient()
            changelog_path = os.path.join(tmpdir, "CHANGELOG.md")
            client._update_changelog(changelog_path, "1.1.0", "Added new feature")

            with open(changelog_path, "r") as f:
                content = f.read()
            assert "# Changelog" in content
            assert "[1.1.0]" in content
            assert "Added new feature" in content

    def test_update_changelog_existing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = MarketClient()
            changelog_path = os.path.join(tmpdir, "CHANGELOG.md")
            with open(changelog_path, "w") as f:
                f.write("# Changelog\n\n## [1.0.0] - 2024-01-01\n\nInitial release.\n")

            client._update_changelog(changelog_path, "1.1.0", "New feature")
            with open(changelog_path, "r") as f:
                content = f.read()
            assert "[1.1.0]" in content
            assert "[1.0.0]" in content
            # 新版本应该在旧版本前面
            assert content.index("[1.1.0]") < content.index("[1.0.0]")


class TestDataModels:
    def test_agent_info(self):
        info = AgentInfo(
            id="agent-1",
            name="test-agent",
            display_name="Test Agent",
            version="1.0.0",
            description="A test agent",
            author="Test Author",
        )
        assert info.name == "test-agent"
        assert info.downloads == 0

    def test_version_info(self):
        v = VersionInfo(version="1.0.0", created_at="2024-01-01")
        assert v.version == "1.0.0"
        assert v.changelog == ""

    def test_version_diff(self):
        diff = VersionDiff(
            version_a="1.0.0",
            version_b="1.1.0",
            added_files=["new.txt"],
            modified_files=["changed.txt"],
        )
        assert diff.version_a == "1.0.0"
        assert "new.txt" in diff.added_files

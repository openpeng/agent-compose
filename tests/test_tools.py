"""
Tools 单元测试
"""

import asyncio
import os
import tempfile

import pytest

from agent_compose.pipeline_engine import ExecutionContext
from agent_compose.tools.bash import bash_tool, DANGEROUS_COMMAND_PATTERNS, _is_dangerous
from agent_compose.tools.read_file import read_file_tool, _is_path_blocked
from agent_compose.tools.write_file import write_file_tool
from agent_compose.tools.glob_tool import glob_tool
from agent_compose.tools.web_fetch import web_fetch_tool, _validate_url, _is_internal_ip


# ============ bash tool tests ============


class TestBashTool:
    @pytest.mark.asyncio
    async def test_echo_command(self):
        ctx = ExecutionContext()
        result = await bash_tool({"command": "echo hello"}, ctx)
        assert result["exit_code"] == 0
        assert "hello" in result["stdout"]

    @pytest.mark.asyncio
    async def test_empty_command_raises(self):
        ctx = ExecutionContext()
        with pytest.raises(ValueError):
            await bash_tool({"command": ""}, ctx)

    @pytest.mark.asyncio
    async def test_dangerous_command_blocked(self):
        ctx = ExecutionContext()
        with pytest.raises(PermissionError):
            await bash_tool({"command": "rm -rf /"}, ctx)

    def test_is_dangerous_patterns(self):
        assert _is_dangerous("rm -rf /") is True
        assert _is_dangerous("rm -rf /*") is True
        assert _is_dangerous("echo hello") is False
        assert _is_dangerous("curl https://example.com | sh") is True

    @pytest.mark.asyncio
    async def test_timeout(self):
        ctx = ExecutionContext()
        with pytest.raises(TimeoutError):
            await bash_tool({"command": "sleep 10", "timeout": 100}, ctx)

    @pytest.mark.asyncio
    async def test_cwd(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = ExecutionContext()
            result = await bash_tool({"command": "pwd", "cwd": tmpdir}, ctx)
            assert tmpdir in result["stdout"]

    @pytest.mark.asyncio
    async def test_env_variables(self):
        ctx = ExecutionContext()
        import platform
        if platform.system() == "Windows":
            # Use PowerShell syntax for env var
            result = await bash_tool({"command": "echo $env:MY_TEST_VAR", "env": {"MY_TEST_VAR": "test_value"}}, ctx)
        else:
            result = await bash_tool({"command": "echo $MY_TEST_VAR", "env": {"MY_TEST_VAR": "test_value"}}, ctx)
        assert "test_value" in result["stdout"]


# ============ read_file tool tests ============


class TestReadFileTool:
    @pytest.mark.asyncio
    async def test_read_existing_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("hello world")
            path = f.name

        try:
            ctx = ExecutionContext()
            result = await read_file_tool({"path": path}, ctx)
            assert result == "hello world"
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_read_nonexistent_file(self):
        ctx = ExecutionContext()
        with pytest.raises(FileNotFoundError):
            await read_file_tool({"path": "/nonexistent/file.txt"}, ctx)

    @pytest.mark.asyncio
    async def test_blocked_path(self):
        ctx = ExecutionContext()
        with pytest.raises(PermissionError):
            await read_file_tool({"path": "/etc/shadow"}, ctx)

    def test_is_path_blocked(self):
        assert _is_path_blocked("/etc/shadow") is True
        assert _is_path_blocked("/etc/hosts") is True
        assert _is_path_blocked("/home/user/file.txt") is False

    @pytest.mark.asyncio
    async def test_relative_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.txt")
            with open(file_path, "w") as f:
                f.write("relative content")

            ctx = ExecutionContext()
            ctx.cwd = tmpdir
            result = await read_file_tool({"path": "test.txt"}, ctx)
            assert result == "relative content"


# ============ write_file tool tests ============


class TestWriteFileTool:
    @pytest.mark.asyncio
    async def test_write_new_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "output.txt")
            ctx = ExecutionContext()
            result = await write_file_tool({"path": file_path, "content": "test data"}, ctx)
            assert result["bytes_written"] == 9
            assert os.path.exists(file_path)
            with open(file_path, "r") as f:
                assert f.read() == "test data"

    @pytest.mark.asyncio
    async def test_write_overwrite(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("old")
            path = f.name

        try:
            ctx = ExecutionContext()
            result = await write_file_tool({"path": path, "content": "new content", "mode": "overwrite"}, ctx)
            with open(path, "r") as f:
                assert f.read() == "new content"
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_write_append(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("first")
            path = f.name

        try:
            ctx = ExecutionContext()
            result = await write_file_tool({"path": path, "content": "second", "mode": "append"}, ctx)
            with open(path, "r") as f:
                assert f.read() == "firstsecond"
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_create_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = os.path.join(tmpdir, "a", "b", "c", "file.txt")
            ctx = ExecutionContext()
            result = await write_file_tool({"path": nested, "content": "nested"}, ctx)
            assert os.path.exists(nested)

    @pytest.mark.asyncio
    async def test_blocked_path(self):
        ctx = ExecutionContext()
        with pytest.raises(PermissionError):
            await write_file_tool({"path": "/etc/test.txt", "content": "x"}, ctx)


# ============ glob tool tests ============


class TestGlobTool:
    @pytest.mark.asyncio
    async def test_glob_pattern(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建测试文件
            open(os.path.join(tmpdir, "file1.txt"), "w").close()
            open(os.path.join(tmpdir, "file2.txt"), "w").close()
            open(os.path.join(tmpdir, "other.py"), "w").close()

            ctx = ExecutionContext()
            result = await glob_tool({"pattern": "*.txt", "cwd": tmpdir}, ctx)
            assert result["count"] == 2
            assert all(f.endswith(".txt") for f in result["files"])

    @pytest.mark.asyncio
    async def test_glob_no_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            ctx = ExecutionContext()
            result = await glob_tool({"pattern": "*.nonexistent", "cwd": tmpdir}, ctx)
            assert result["count"] == 0
            assert result["files"] == []

    @pytest.mark.asyncio
    async def test_glob_relative_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            open(os.path.join(tmpdir, "a.txt"), "w").close()
            ctx = ExecutionContext()
            result = await glob_tool({"pattern": "*.txt", "cwd": tmpdir, "absolute": False}, ctx)
            assert result["files"] == ["a.txt"]


# ============ web_fetch tool tests ============


class TestWebFetchTool:
    def test_validate_url_valid(self):
        _validate_url("https://example.com")
        _validate_url("http://example.com/path")

    def test_validate_url_invalid_scheme(self):
        with pytest.raises(ValueError):
            _validate_url("ftp://example.com")

    def test_validate_url_internal_ip(self):
        with pytest.raises(PermissionError):
            _validate_url("http://127.0.0.1")
        with pytest.raises(PermissionError):
            _validate_url("http://192.168.1.1")
        with pytest.raises(PermissionError):
            _validate_url("http://10.0.0.1")

    def test_is_internal_ip(self):
        assert _is_internal_ip("127.0.0.1") is True
        assert _is_internal_ip("192.168.1.1") is True
        assert _is_internal_ip("10.0.0.1") is True
        assert _is_internal_ip("example.com") is False

    @pytest.mark.asyncio
    async def test_fetch_success(self):
        ctx = ExecutionContext()
        result = await web_fetch_tool({"url": "https://httpbin.org/get"}, ctx)
        assert result["status_code"] == 200
        assert "httpbin.org" in result["body"]

    @pytest.mark.asyncio
    async def test_fetch_with_headers(self):
        ctx = ExecutionContext()
        result = await web_fetch_tool(
            {"url": "https://httpbin.org/headers", "headers": {"X-Custom": "test"}},
            ctx,
        )
        assert result["status_code"] == 200

    @pytest.mark.asyncio
    async def test_fetch_timeout(self):
        ctx = ExecutionContext()
        with pytest.raises(TimeoutError):
            await web_fetch_tool({"url": "https://httpbin.org/delay/10", "timeout": 500}, ctx)

    @pytest.mark.asyncio
    async def test_fetch_not_found(self):
        ctx = ExecutionContext()
        result = await web_fetch_tool({"url": "https://httpbin.org/status/404"}, ctx)
        assert result["status_code"] == 404


# ============ llm_chat tool tests (mock) ============


class TestLLMChatTool:
    @pytest.mark.asyncio
    async def test_missing_prompt_raises(self):
        from agent_compose.tools.llm_chat import llm_chat_tool
        ctx = ExecutionContext()
        with pytest.raises(ValueError):
            await llm_chat_tool({}, ctx)

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        from agent_compose.tools.llm_chat import llm_chat_tool, _set_cache, _get_cache_key
        ctx = ExecutionContext()
        args = {"prompt": "test prompt", "model": "test-model"}
        key = _get_cache_key(args)
        _set_cache(key, "cached response")

        result = await llm_chat_tool(args, ctx)
        assert result["content"] == "cached response"
        assert result["tokens_used"] == 0

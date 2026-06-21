"""
Adapters 单元测试
"""

import json
import os
import tempfile

import pytest

from agent_compose.adapters.base import parse_frontmatter, slugify, extract_description
from agent_compose.adapters import (
    CursorAdapter,
    ClaudeAdapter,
    CodeBuddyAdapter,
    GitHubAdapter,
    VSCodeAdapter,
    JetBrainsAdapter,
    OpenAIGPTsAdapter,
    GenericMarkdownAdapter,
    AdapterRegistry,
    create_default_registry,
)


# ============ Base utility tests ============


class TestBaseUtils:
    def test_slugify(self):
        assert slugify("Hello World") == "hello-world"
        assert slugify("My Agent v2!") == "my-agent-v2"
        assert slugify("  spaces  ") == "spaces"

    def test_parse_frontmatter(self):
        content = "---\nname: Test Agent\nversion: 1.0.0\n---\n\n# Instructions\n\nDo something."
        fm, body = parse_frontmatter(content)
        assert fm["name"] == "Test Agent"
        assert fm["version"] == "1.0.0"
        assert "# Instructions" in body

    def test_parse_frontmatter_no_frontmatter(self):
        content = "# Title\n\nBody text"
        fm, body = parse_frontmatter(content)
        assert fm == {}
        assert body == content

    def test_extract_description(self):
        content = "# Title\n\nThis is the description paragraph.\n\nMore content here."
        desc = extract_description(content)
        assert "description paragraph" in desc


# ============ Cursor Adapter tests ============


class TestCursorAdapter:
    def test_can_import(self):
        adapter = CursorAdapter()
        assert adapter.can_import(".cursor/commands/my-agent.md") is True
        assert adapter.can_import(".cursor/commands/my-agent.txt") is False
        assert adapter.can_import("other/path.md") is False

    def test_import_from(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cursor_dir = os.path.join(tmpdir, ".cursor", "commands")
            os.makedirs(cursor_dir)
            path = os.path.join(cursor_dir, "my-agent.md")
            with open(path, "w") as f:
                f.write("---\nname: My Cursor Agent\nversion: 1.0.0\n---\n\n# Instructions\n\nDo something useful.")

            adapter = CursorAdapter()
            result = adapter.import_from(path)
            assert result["identity"]["name"] == "my-agent"
            assert result["identity"]["display_name"] == "My Cursor Agent"
            assert result["compatibility"]["source"] == "cursor"
            assert "Do something useful" in result["instructions"]["content"]


# ============ Claude Adapter tests ============


class TestClaudeAdapter:
    def test_can_import(self):
        adapter = ClaudeAdapter()
        assert adapter.can_import(".claude/commands/test.md") is True
        assert adapter.can_import("other/path.md") is False

    def test_import_from(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("---\nname: Claude Agent\n---\n\n# /claude-agent -- Claude Agent\n\n## Description\n\nA test agent.")
            path = f.name

        try:
            adapter = ClaudeAdapter()
            result = adapter.import_from(path)
            assert result["identity"]["display_name"] == "Claude Agent"
            assert result["compatibility"]["source"] == "claude_code"
        finally:
            os.unlink(path)


# ============ CodeBuddy Adapter tests ============


class TestCodeBuddyAdapter:
    def test_can_import(self):
        adapter = CodeBuddyAdapter()
        assert adapter.can_import(".codebuddy/skills/my-skill/SKILL.md") is True
        assert adapter.can_import(".codebuddy/skills/my-skill/other.md") is False

    def test_import_from(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = os.path.join(tmpdir, "my-skill")
            os.makedirs(skill_dir)
            skill_path = os.path.join(skill_dir, "SKILL.md")
            with open(skill_path, "w") as f:
                f.write("---\nname: My Skill\ndisplay_name: My Display Name\ndescription: A test skill\ntags: [test, demo]\n---\n\n# Skill Instructions\n\nDo the skill.")

            adapter = CodeBuddyAdapter()
            result = adapter.import_from(skill_path)
            assert result["identity"]["name"] == "my-skill"
            assert result["identity"]["display_name"] == "My Display Name"
            assert "codebuddy" in result["identity"]["tags"]

    def test_import_from_missing_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            skill_dir = os.path.join(tmpdir, "bad-skill")
            os.makedirs(skill_dir)
            skill_path = os.path.join(skill_dir, "SKILL.md")
            with open(skill_path, "w") as f:
                f.write("# No Frontmatter\n\nJust content.")

            adapter = CodeBuddyAdapter()
            with pytest.raises(ValueError):
                adapter.import_from(skill_path)


# ============ GitHub Adapter tests ============


class TestGitHubAdapter:
    def test_can_import(self):
        adapter = GitHubAdapter()
        assert adapter.can_import(".github/agents/my-agent.md") is True
        assert adapter.can_import("other/path.md") is False

    def test_import_from(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            github_dir = os.path.join(tmpdir, ".github", "agents")
            os.makedirs(github_dir)
            path = os.path.join(github_dir, "my-agent.md")
            with open(path, "w") as f:
                f.write("---\nname: GitHub Agent\n---\n\n# GitHub Agent\n\nHelp with GitHub tasks.")

            adapter = GitHubAdapter()
            result = adapter.import_from(path)
            assert result["identity"]["name"] == "my-agent"
            assert result["compatibility"]["source"] == "github_copilot"


# ============ VS Code Adapter tests ============


class TestVSCodeAdapter:
    def test_can_import(self):
        adapter = VSCodeAdapter()
        assert adapter.can_import(".vscode/agents/my-agent.md") is True
        assert adapter.can_import("other/path.md") is False

    def test_import_from_with_tools(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            vscode_dir = os.path.join(tmpdir, ".vscode", "agents")
            os.makedirs(vscode_dir)
            path = os.path.join(vscode_dir, "my-agent.md")
            with open(path, "w") as f:
                f.write("---\nname: VS Code Agent\ntools: [bash, read_file]\n---\n\n# VS Code Agent\n\nHelp with coding.")

            adapter = VSCodeAdapter()
            result = adapter.import_from(path)
            assert result["identity"]["name"] == "my-agent"
            assert "tool:bash" in result["capabilities"]
            assert "tool:read_file" in result["capabilities"]
            assert "vscode" in result["identity"]["tags"]


# ============ JetBrains Adapter tests ============


class TestJetBrainsAdapter:
    def test_can_import(self):
        adapter = JetBrainsAdapter()
        assert adapter.can_import(".idea/ai-assistant/my-prompt.md") is True
        assert adapter.can_import("other/path.md") is False

    def test_import_from_with_variables(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jetbrains_dir = os.path.join(tmpdir, ".idea", "ai-assistant")
            os.makedirs(jetbrains_dir)
            path = os.path.join(jetbrains_dir, "my-prompt.md")
            with open(path, "w") as f:
                f.write("---\nname: JetBrains Prompt\n---\n\n# Prompt\n\nProcess {{file_name}} with {{language}}.")

            adapter = JetBrainsAdapter()
            result = adapter.import_from(path)
            assert result["identity"]["name"] == "my-prompt"
            assert "variables" in result
            assert "file_name" in result["variables"]
            assert "language" in result["variables"]


# ============ OpenAI GPTs Adapter tests ============


class TestOpenAIGPTsAdapter:
    def test_can_import(self):
        adapter = OpenAIGPTsAdapter()
        assert adapter.can_import("gpts/my-gpt.json") is True
        assert adapter.can_import("other/path.json") is False

    def test_import_from(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "name": "My GPT",
                "description": "A test GPT",
                "instructions": "Be helpful.",
                "capabilities": ["web_browsing", "code_interpreter"],
                "conversation_starters": ["Hello", "Help me"],
            }, f)
            path = f.name

        try:
            adapter = OpenAIGPTsAdapter()
            result = adapter.import_from(path)
            assert result["identity"]["name"] == "my-gpt"
            assert result["identity"]["display_name"] == "My GPT"
            assert "web_search" in result["capabilities"]
            assert "code_execution" in result["capabilities"]
            assert "Conversation Starters" in result["instructions"]["content"]
        finally:
            os.unlink(path)


# ============ Generic Markdown Adapter tests ============


class TestGenericMarkdownAdapter:
    def test_can_import(self):
        adapter = GenericMarkdownAdapter()
        # can_import 需要文件真实存在
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("# Test")
            path = f.name
        try:
            assert adapter.can_import(path) is True
            assert adapter.can_import("some/path/file.txt") is False
        finally:
            os.unlink(path)

    def test_import_from(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "my-agent.md")
            with open(path, "w") as f:
                f.write("---\nname: Generic Agent\nauthor: Test Author\n---\n\n# Generic Agent\n\nSome instructions.")

            adapter = GenericMarkdownAdapter()
            result = adapter.import_from(path)
            assert result["identity"]["name"] == "my-agent"
            assert result["identity"]["author"] == "Test Author"
            assert result["compatibility"]["source"] == "generic_markdown"


# ============ Adapter Registry tests ============


class TestAdapterRegistry:
    def test_register_and_find(self):
        registry = AdapterRegistry()
        adapter = CursorAdapter()
        registry.register(adapter)

        found = registry.find_adapter(".cursor/commands/test.md")
        assert found is adapter

    def test_find_no_match(self):
        registry = AdapterRegistry()
        found = registry.find_adapter("unknown/path.txt")
        assert found is None

    def test_import_agent(self):
        registry = AdapterRegistry()
        registry.register(GenericMarkdownAdapter())

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "my-test.md")
            with open(path, "w") as f:
                f.write("# Test\n\nContent.")

            result = registry.import_agent(path)
            assert result["identity"]["name"] == "my-test"

    def test_import_agent_no_adapter(self):
        registry = AdapterRegistry()
        with pytest.raises(ValueError):
            registry.import_agent("unknown.txt")

    def test_list_adapters(self):
        registry = create_default_registry()
        adapters = registry.list_adapters()
        names = [a["name"] for a in adapters]
        assert "cursor" in names
        assert "claude_code" in names
        assert "github_copilot" in names
        assert "vscode_agent_mode" in names
        assert "jetbrains_ai_assistant" in names
        assert "openai_gpts" in names
        assert "generic_markdown" in names

    def test_scan_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # 创建一些文件
            open(os.path.join(tmpdir, "agent.md"), "w").close()
            open(os.path.join(tmpdir, "agent.txt"), "w").close()
            subdir = os.path.join(tmpdir, ".cursor", "commands")
            os.makedirs(subdir)
            open(os.path.join(subdir, "cmd.md"), "w").close()

            registry = create_default_registry()
            results = registry.scan_directory(tmpdir)
            assert len(results) >= 2  # agent.md and cmd.md

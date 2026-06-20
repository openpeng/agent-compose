"""Pytest fixtures and helpers for agent-compose tests."""

import os
import sys
import tempfile
from pathlib import Path

TEST_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(TEST_ROOT))


def make_temp_dir() -> Path:
    """Create a temporary directory for testing."""
    tmp = tempfile.mkdtemp(prefix="agent_compose_test_")
    return Path(tmp)


def write_yaml(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


__all__ = ["TEST_ROOT", "make_temp_dir", "write_yaml"]

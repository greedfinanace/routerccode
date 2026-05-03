"""Tests for the tool execution engine."""

import pytest
from pathlib import Path

from openrouter_agent.tools import ToolExecutor


@pytest.fixture
def executor(tmp_path):
    return ToolExecutor(working_dir=tmp_path)


@pytest.fixture
def sample_file(tmp_path):
    f = tmp_path / "test.py"
    f.write_text("def hello():\n    print('Hello, World!')\n\nhello()\n")
    return f


class TestReadFile:
    def test_read_existing_file(self, executor, sample_file):
        import asyncio
        result = asyncio.run(executor.execute("read_file", {"file_path": str(sample_file)}))
        assert "def hello" in result
        assert "1:" in result

    def test_read_nonexistent_file(self, executor):
        import asyncio
        result = asyncio.run(executor.execute("read_file", {"file_path": "nonexistent.py"}))
        assert "not found" in result.lower()

    def test_read_line_range(self, executor, sample_file):
        import asyncio
        result = asyncio.run(executor.execute("read_file", {
            "file_path": str(sample_file),
            "start_line": 1,
            "end_line": 2,
        }))
        assert "def hello" in result
        assert "hello()" not in result or "print" in result


class TestApplyDiff:
    def test_successful_diff(self, executor, sample_file):
        import asyncio
        result = asyncio.run(executor.execute("apply_diff", {
            "file_path": str(sample_file),
            "search": "Hello, World!",
            "replace": "Hi there!",
        }))
        assert "Applied diff" in result
        assert "Hi there!" in sample_file.read_text()

    def test_diff_not_found(self, executor, sample_file):
        import asyncio
        result = asyncio.run(executor.execute("apply_diff", {
            "file_path": str(sample_file),
            "search": "NONEXISTENT TEXT",
            "replace": "replacement",
        }))
        assert "not found" in result.lower()


class TestRunCommand:
    def test_successful_command(self, executor):
        import asyncio
        result = asyncio.run(executor.execute("run_command", {
            "command": "echo hello",
            "timeout": 10,
        }))
        assert "hello" in result
        assert "exit code: 0" in result

    def test_timeout(self, executor):
        import asyncio
        result = asyncio.run(executor.execute("run_command", {
            "command": "ping -n 100 127.0.0.1",
            "timeout": 1,
        }))
        assert "timed out" in result.lower()


class TestListDirectory:
    def test_list_dir(self, executor, sample_file):
        import asyncio
        result = asyncio.run(executor.execute("list_directory", {
            "path": str(sample_file.parent),
        }))
        assert "test.py" in result


class TestSearchFiles:
    def test_search_pattern(self, executor, sample_file):
        import asyncio
        result = asyncio.run(executor.execute("search_files", {
            "pattern": "def hello",
            "path": str(sample_file.parent),
        }))
        assert "test.py" in result
        assert "def hello" in result

"""Tests for codebase mapping."""

import pytest
from pathlib import Path
from openrouter_agent.codebase_map import extract_signatures, generate_codebase_map


class TestExtractSignatures:
    def test_function_signature(self):
        source = "def hello(name: str) -> str:\n    return f'Hello {name}'\n"
        sigs = extract_signatures(source)
        assert any("def hello" in s for s in sigs)

    def test_class_signature(self):
        source = "class MyClass:\n    pass\n"
        sigs = extract_signatures(source)
        assert any("class MyClass" in s for s in sigs)

    def test_async_function(self):
        source = "async def fetch(url):\n    pass\n"
        sigs = extract_signatures(source)
        assert any("async def fetch" in s for s in sigs)

    def test_invalid_syntax(self):
        sigs = extract_signatures("this is not python %%%")
        assert sigs == []


class TestCodebaseMap:
    def test_generate_map(self, tmp_path):
        # Create some Python files
        (tmp_path / "main.py").write_text("def main():\n    pass\n")
        (tmp_path / "utils.py").write_text("class Helper:\n    pass\n")

        result = generate_codebase_map(tmp_path)
        assert "main.py" in result
        assert "utils.py" in result
        assert "def main" in result
        assert "class Helper" in result

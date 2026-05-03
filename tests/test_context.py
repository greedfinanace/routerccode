"""Tests for context management and compression."""

import pytest
from openrouter_agent.context import ContextManager


class TestContextManager:
    def test_add_messages(self):
        ctx = ContextManager()
        ctx.add_user_message("Hello")
        ctx.add_assistant_message("Hi there")
        assert len(ctx.messages) == 2
        assert ctx.messages[0]["role"] == "user"
        assert ctx.messages[1]["role"] == "assistant"

    def test_truncate_output(self):
        long_text = "x" * 20000
        result = ContextManager._truncate_output(long_text, max_chars=1000)
        assert len(result) < len(long_text)
        assert "truncated" in result

    def test_short_output_not_truncated(self):
        short_text = "hello world"
        result = ContextManager._truncate_output(short_text)
        assert result == short_text

    def test_clear(self):
        ctx = ContextManager()
        ctx.add_user_message("Hello")
        ctx.clear()
        assert len(ctx.messages) == 0

    def test_build_payload(self):
        ctx = ContextManager()
        ctx.add_user_message("Test query")
        payload = ctx.build_payload()
        assert "messages" in payload
        # System prompt should be first
        assert payload["messages"][0]["role"] == "system"

    def test_token_counting(self):
        ctx = ContextManager()
        count = ctx.count_tokens("Hello, world!")
        assert count > 0
        assert isinstance(count, int)

    def test_snip_old_turns(self):
        ctx = ContextManager()
        for i in range(30):
            ctx.add_user_message(f"Message {i}")
        ctx._snip_old_turns()
        assert len(ctx.messages) == 21  # 20 recent + 1 marker
        assert "truncated" in ctx.messages[0]["content"]

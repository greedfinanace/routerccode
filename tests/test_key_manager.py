"""Tests for API key management."""

import os
import pytest
from openrouter_agent.key_manager import APIKeyManager


class TestAPIKeyManager:
    def test_explicit_key_priority(self):
        mgr = APIKeyManager(explicit_key="sk-test-123")
        assert mgr.get_api_key() == "sk-test-123"

    def test_env_var_priority(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-env-456")
        mgr = APIKeyManager()
        assert mgr.get_api_key() == "sk-env-456"

    def test_mask_key(self):
        key = "sk-or-v1-abcdef1234567890abcdef1234567890"
        masked = APIKeyManager.mask(key)
        assert "****" in masked
        assert key not in masked
        assert masked.startswith("sk-or-v1")

    def test_mask_short_key(self):
        masked = APIKeyManager.mask("short")
        assert masked == "****"

    def test_sanitize_env(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "secret")
        env = APIKeyManager.sanitize_env()
        assert "OPENROUTER_API_KEY" not in env

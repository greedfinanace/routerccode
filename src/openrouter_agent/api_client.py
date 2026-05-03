"""
OpenRouter API Client

Async streaming client for the OpenRouter API (NOT OpenAI).
OpenRouter-specific features:
- Uses openrouter.ai/api/v1 endpoint
- HTTP-Referer & X-Title headers (required by OpenRouter)
- Provider-specific routing (anthropic cache_control, etc.)
- Model listing via /models endpoint
- Real cost tracking from OpenRouter usage data
- Retry with exponential backoff
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Optional

import httpx
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class ChatMessage(BaseModel):
    role: str
    content: str | list[dict[str, Any]] = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    cache_control: Optional[dict[str, str]] = None


class UsageStats(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_cost_usd: float = 0.0


class ModelInfo(BaseModel):
    """Info about a model available on OpenRouter."""
    id: str
    name: str = ""
    context_length: int = 0
    pricing_prompt: float = 0.0   # per million tokens
    pricing_completion: float = 0.0
    top_provider: str = ""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-sonnet-4"
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0


class OpenRouterClient:
    """
    Async client for OpenRouter chat completions.

    This is NOT an OpenAI client - it uses OpenRouter-specific headers,
    provider routing, model discovery, and cost tracking.
    """

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str = OPENROUTER_BASE_URL,
        timeout: float = 120.0,
    ):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.timeout = timeout
        self.usage = UsageStats()
        self._http_client: Optional[httpx.AsyncClient] = None
        self._models_cache: list[ModelInfo] = []
        self._request_count: int = 0
        self._start_time: float = time.time()

    # -- HTTP lifecycle -----------------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    # OpenRouter-specific required headers
                    "HTTP-Referer": "https://github.com/greedfinanace/routerccode",
                    "X-Title": "RouterCode CLI",
                },
                timeout=httpx.Timeout(self.timeout, connect=10.0),
            )
        return self._http_client

    async def close(self) -> None:
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    # -- OpenRouter model discovery -----------------------------------------

    async def list_models(self, query: str = "") -> list[ModelInfo]:
        """
        Fetch available models from OpenRouter's /models endpoint.
        This is an OpenRouter-exclusive feature - OpenAI doesn't expose this.
        """
        client = await self._get_client()
        resp = await client.get("/models")
        resp.raise_for_status()
        data = resp.json()

        models = []
        for m in data.get("data", []):
            pricing = m.get("pricing", {})
            info = ModelInfo(
                id=m.get("id", ""),
                name=m.get("name", m.get("id", "")),
                context_length=m.get("context_length", 0),
                pricing_prompt=float(pricing.get("prompt", 0)) * 1_000_000,
                pricing_completion=float(pricing.get("completion", 0)) * 1_000_000,
                top_provider=m.get("top_provider", {}).get("name", ""),
            )
            if query:
                if query.lower() not in info.id.lower() and query.lower() not in info.name.lower():
                    continue
            models.append(info)

        self._models_cache = models
        return models

    async def get_model_info(self) -> Optional[ModelInfo]:
        """Get info about the currently selected model."""
        if not self._models_cache:
            await self.list_models()
        for m in self._models_cache:
            if m.id == self.model:
                return m
        return None

    # -- Payload construction -----------------------------------------------

    def _build_request_body(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Build the JSON body for OpenRouter /chat/completions.
        Uses OpenRouter-specific provider routing and cache control.
        """
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": 0.0,
        }

        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"

        # OpenRouter provider-specific routing
        # This is how you pass provider-specific params through OpenRouter
        provider_prefs: dict[str, Any] = {}

        if "anthropic" in self.model:
            # Enable prompt caching for Anthropic models via OpenRouter
            provider_prefs["anthropic"] = {
                "cache_control": {"type": "ephemeral"}
            }

        if "google" in self.model or "gemini" in self.model:
            # Google models through OpenRouter
            provider_prefs["google"] = {
                "safety_settings": [
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
                ]
            }

        if provider_prefs:
            body["provider"] = provider_prefs

        # OpenRouter-specific: transforms (optional, for compatibility)
        # This tells OpenRouter to handle tool calling format conversion
        body["route"] = "fallback"

        return body

    # -- Streaming ----------------------------------------------------------

    async def stream_chat(
        self,
        payload: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Stream a chat completion from OpenRouter.

        Yields dicts with:
          {"type": "text", "content": "..."}
          {"type": "tool_call", "tool_call": {"name": ..., "parameters": ...}}
          {"type": "usage", "usage": {...}}
        """
        messages = payload.get("messages", [])
        tools = payload.get("tools", None)
        body = self._build_request_body(messages, tools)

        client = await self._get_client()
        self._request_count += 1

        # Accumulate partial tool call JSON
        tool_call_buffer: dict[int, dict[str, Any]] = {}

        # Retry logic with exponential backoff
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                async with client.stream(
                    "POST",
                    "/chat/completions",
                    json=body,
                ) as response:
                    if response.status_code == 429:
                        # OpenRouter rate limit - back off
                        wait = RETRY_BASE_DELAY * (2 ** attempt)
                        yield {"type": "text", "content": f"\n[rate limited, retrying in {wait:.0f}s...]\n"}
                        await asyncio.sleep(wait)
                        continue

                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as e:
                        await e.response.aread()
                        error_text = e.response.text
                        
                        # Fallback: if the model doesn't support tools, strip them and retry.
                        if e.response.status_code == 404 and "tool_choice" in error_text:
                            if "tools" in body:
                                del body["tools"]
                            if "tool_choice" in body:
                                del body["tool_choice"]
                            yield {"type": "text", "content": "\n[dim][!] Model does not support tools. Falling back to text-only mode...[/dim]\n"}
                            continue
                            
                        raise

                    async for line in response.aiter_lines():
                        line = line.strip()
                        if not line or line == "data: [DONE]":
                            continue
                        if line.startswith("data: "):
                            line = line[6:]

                        try:
                            chunk = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        # Usage info (OpenRouter returns this in the final chunk)
                        if "usage" in chunk:
                            u = chunk["usage"]
                            self.usage.prompt_tokens += u.get("prompt_tokens", 0)
                            self.usage.completion_tokens += u.get("completion_tokens", 0)
                            self.usage.cache_read_tokens += u.get("cache_read_input_tokens", 0)
                            self.usage.cache_write_tokens += u.get("cache_creation_input_tokens", 0)
                            # OpenRouter sometimes includes cost directly
                            if "total_cost" in u:
                                self.usage.total_cost_usd += float(u["total_cost"])
                            yield {"type": "usage", "usage": u}

                        choices = chunk.get("choices", [])
                        if not choices:
                            continue

                        delta = choices[0].get("delta", {})

                        # Text content
                        if delta.get("content"):
                            yield {"type": "text", "content": delta["content"]}

                        # Reasoning content
                        if delta.get("reasoning"):
                            yield {"type": "reasoning", "content": delta["reasoning"]}

                        # Tool calls (streamed incrementally)
                        if "tool_calls" in delta:
                            for tc_delta in delta["tool_calls"]:
                                idx = tc_delta.get("index", 0)
                                if idx not in tool_call_buffer:
                                    tool_call_buffer[idx] = {
                                        "id": "",
                                        "name": "",
                                        "parameters_json": "",
                                    }

                                if "id" in tc_delta and tc_delta["id"]:
                                    tool_call_buffer[idx]["id"] = tc_delta["id"]

                                fn = tc_delta.get("function", {})
                                if "name" in fn and fn["name"]:
                                    tool_call_buffer[idx]["name"] = fn["name"]
                                if "arguments" in fn and fn["arguments"]:
                                    tool_call_buffer[idx]["parameters_json"] += fn["arguments"]

                        # Check finish reason
                        finish = choices[0].get("finish_reason")
                        if finish == "tool_calls":
                            for idx in sorted(tool_call_buffer.keys()):
                                tc = tool_call_buffer[idx]
                                try:
                                    params = json.loads(tc["parameters_json"])
                                except json.JSONDecodeError:
                                    params = {"_raw": tc["parameters_json"]}

                                yield {
                                    "type": "tool_call",
                                    "tool_call": {
                                        "id": tc["id"] or f"call_{idx}",
                                        "name": tc["name"],
                                        "parameters": params,
                                        "arguments": tc["parameters_json"],
                                    },
                                }
                            tool_call_buffer.clear()

                    # Success - break retry loop
                    break

            except httpx.HTTPStatusError as e:
                last_error = e
                if attempt < MAX_RETRIES - 1:
                    wait = RETRY_BASE_DELAY * (2 ** attempt)
                    await asyncio.sleep(wait)
                else:
                    raise

    # -- Non-streaming helper (used by compaction) --------------------------

    async def chat(
        self,
        messages: list[dict[str, Any]],
        model_override: str | None = None,
    ) -> str:
        """
        Single-turn non-streaming call via OpenRouter.
        Used for context compaction (can target a cheaper model).
        """
        model = model_override or self.model
        body = {
            "model": model,
            "messages": messages,
            "stream": False,
            "temperature": 0.0,
            "max_tokens": 2048,
            "route": "fallback",
        }

        client = await self._get_client()
        resp = await client.post("/chat/completions", json=body)
        resp.raise_for_status()
        data = resp.json()

        # Track usage
        if "usage" in data:
            u = data["usage"]
            self.usage.prompt_tokens += u.get("prompt_tokens", 0)
            self.usage.completion_tokens += u.get("completion_tokens", 0)

        return data["choices"][0]["message"]["content"]

    # -- Cost estimation ----------------------------------------------------

    def estimate_cost(self) -> dict[str, Any]:
        """
        Estimate session cost based on OpenRouter pricing.
        OpenRouter charges per-token, not per-request.
        """
        elapsed = time.time() - self._start_time
        minutes = elapsed / 60

        # If we have real cost data from OpenRouter
        if self.usage.total_cost_usd > 0:
            return {
                "total_cost_usd": self.usage.total_cost_usd,
                "prompt_tokens": self.usage.prompt_tokens,
                "completion_tokens": self.usage.completion_tokens,
                "cache_read_tokens": self.usage.cache_read_tokens,
                "requests": self._request_count,
                "elapsed_minutes": round(minutes, 1),
                "source": "openrouter_reported",
            }

        # Estimate from known pricing (rough defaults)
        PRICING = {
            "anthropic/claude-sonnet-4": (3.0, 15.0),
            "anthropic/claude-opus-4": (15.0, 75.0),
            "google/gemini-2.5-pro": (1.25, 10.0),
            "google/gemini-2.5-flash": (0.15, 0.60),
            "openai/gpt-4o": (2.50, 10.0),
            "openai/gpt-4o-mini": (0.15, 0.60),
            "meta-llama/llama-4-maverick": (0.20, 0.60),
        }

        prompt_rate, completion_rate = PRICING.get(self.model, (3.0, 15.0))

        # Cache reads are 90% cheaper on Anthropic via OpenRouter
        cache_savings = 0.0
        if self.usage.cache_read_tokens > 0:
            cache_savings = (self.usage.cache_read_tokens / 1_000_000) * prompt_rate * 0.9

        prompt_cost = (self.usage.prompt_tokens / 1_000_000) * prompt_rate
        completion_cost = (self.usage.completion_tokens / 1_000_000) * completion_rate
        total = prompt_cost + completion_cost - cache_savings

        return {
            "total_cost_usd": round(total, 4),
            "prompt_cost_usd": round(prompt_cost, 4),
            "completion_cost_usd": round(completion_cost, 4),
            "cache_savings_usd": round(cache_savings, 4),
            "prompt_tokens": self.usage.prompt_tokens,
            "completion_tokens": self.usage.completion_tokens,
            "cache_read_tokens": self.usage.cache_read_tokens,
            "requests": self._request_count,
            "elapsed_minutes": round(minutes, 1),
            "model": self.model,
            "source": "estimated",
        }

"""Provider selection. Honors LLM_PROVIDER but falls back to the mock provider
when the configured provider has no API key (so the app runs key-free)."""

from __future__ import annotations

from functools import lru_cache

from app.agent.providers.base import EmitFn, LLMResponse, Provider, ToolCall, Usage
from app.agent.providers.mock import MockProvider
from app.config import settings

__all__ = [
    "Provider",
    "LLMResponse",
    "ToolCall",
    "Usage",
    "EmitFn",
    "get_provider",
    "get_provider_for",
]


@lru_cache
def get_provider() -> Provider:
    """Process-wide provider (reuses one SDK HTTP client across requests)."""
    provider = settings.effective_provider
    if provider == "anthropic":
        from app.agent.providers.anthropic import AnthropicProvider

        return AnthropicProvider()
    if provider == "openai":
        from app.agent.providers.openai import OpenAIProvider

        return OpenAIProvider()
    return MockProvider()


def get_provider_for(provider_name: str | None, api_key: str | None) -> Provider:
    """Build a provider for a caller-supplied key (BYOK — bring your own key).

    Used by the public demo so a visitor can drive the agent with their OWN
    Anthropic/OpenAI key, and the server never pays. The returned provider is
    transient and NOT cached: the key lives only for this request, is never
    persisted, and the SDK client is discarded when the request ends.

    Falls back to the shared default ``get_provider()`` (which itself degrades
    to the free mock) whenever no usable key/provider pair is supplied.
    """
    name = (provider_name or "").strip().lower()
    key = (api_key or "").strip()
    if not key:
        return get_provider()
    if name == "anthropic":
        from app.agent.providers.anthropic import AnthropicProvider

        return AnthropicProvider(api_key=key)
    if name == "openai":
        from app.agent.providers.openai import OpenAIProvider

        return OpenAIProvider(api_key=key)
    # Unknown/empty provider name with a stray key — stay safe, use the default.
    return get_provider()

"""BYOK provider selection (DB-free, no network — clients are built, never called).

Locks in the demo's bring-your-own-key contract: a caller-supplied key builds a
transient real provider; anything missing/unknown falls back to the safe default
(which itself degrades to the free mock when the server has no keys configured).
"""

from __future__ import annotations

import pytest

from app.agent.providers import get_provider_for
from app.agent.providers.mock import MockProvider


def test_empty_key_falls_back_to_default() -> None:
    # No server keys are set in the test env, so the default is the mock.
    assert isinstance(get_provider_for("anthropic", ""), MockProvider)
    assert isinstance(get_provider_for("openai", "   "), MockProvider)
    assert isinstance(get_provider_for(None, None), MockProvider)


def test_unknown_provider_with_stray_key_is_safe() -> None:
    assert isinstance(get_provider_for("bogus", "x"), MockProvider)


@pytest.mark.parametrize(
    ("name", "expected"),
    [("anthropic", "anthropic"), ("OpenAI", "openai")],
)
def test_byok_builds_named_provider(name: str, expected: str) -> None:
    # A real key builds the matching provider (case-insensitive). The SDK client
    # is constructed but never invoked, so a fake key is fine here.
    provider = get_provider_for(name, "sk-fake-byok-key")
    assert provider.name == expected


def test_byok_provider_is_not_cached() -> None:
    # Each BYOK call must build a fresh provider — caching would retain a
    # visitor's key across requests.
    a = get_provider_for("anthropic", "sk-fake-a")
    b = get_provider_for("anthropic", "sk-fake-b")
    assert a is not b

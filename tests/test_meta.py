"""Meta / observability endpoints: GET /v1/tools and GET /v1/usage.

DB-free: auth + endpoint wiring + response shaping via fakes. The real GROUP BY /
func.date aggregation SQL is verified live against Postgres.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

from httpx import AsyncClient

from app.db import get_session
from app.main import app
from app.models import Workspace
from app.tools import get_registry

_EXPECTED_TOOLS = {
    "search_help_docs", "lookup_order", "create_ticket",
    "send_email", "escalate_to_human", "initiate_refund",
}


def _ws() -> Workspace:
    return Workspace(id=uuid.uuid4(), name="Acme Outdoors", api_key_hash="dummy")


# --------------------------------------------------------------------------- #
# GET /v1/tools
# --------------------------------------------------------------------------- #
async def test_tools_requires_auth(client: AsyncClient, session_returns: Callable) -> None:
    session_returns(None)
    assert (await client.get("/v1/tools")).status_code == 401


async def test_tools_lists_every_registered_tool(
    client: AsyncClient, session_returns: Callable
) -> None:
    session_returns(_ws())
    resp = await client.get("/v1/tools", headers={"Authorization": "Bearer k"})
    assert resp.status_code == 200
    body = resp.json()
    assert {t["name"] for t in body} == _EXPECTED_TOOLS
    for t in body:
        assert t["description"]
        assert t["parameters_schema"]["type"] == "object"  # pydantic JSON schema
    # endpoint reflects the live registry, not a hardcoded list
    assert len(body) == len(get_registry().names())


# --------------------------------------------------------------------------- #
# GET /v1/usage
# --------------------------------------------------------------------------- #
class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def one(self) -> Any:
        return self._rows[0]

    def all(self) -> list[Any]:
        return self._rows


class _UsageSession:
    """Serves the auth lookup (scalar→ws) then aggregate_usage's three execute()s."""

    def __init__(self, ws: Workspace, exec_results: list[_Result]) -> None:
        self._ws = ws
        self._exec = list(exec_results)

    async def scalar(self, *a: Any, **k: Any) -> Any:
        return self._ws

    async def execute(self, *a: Any, **k: Any) -> _Result:
        return self._exec.pop(0)


async def test_usage_requires_auth(client: AsyncClient, session_returns: Callable) -> None:
    session_returns(None)
    assert (await client.get("/v1/usage")).status_code == 401


async def test_usage_endpoint_shapes_totals_and_rollups(client: AsyncClient) -> None:
    ws = _ws()
    results = [
        _Result([(Decimal("0.006000"), 1500, 3)]),  # totals: cost, tokens, count
        _Result([("claude-haiku-4-5", Decimal("0.006000"), 1500, 3)]),  # by_model
        _Result([(date(2026, 6, 1), Decimal("0.006000"), 1500, 3)]),  # by_day
    ]

    async def _dep():
        yield _UsageSession(ws, results)

    app.dependency_overrides[get_session] = _dep
    try:
        resp = await client.get("/v1/usage", headers={"Authorization": "Bearer k"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    # numeric regardless of float/str JSON encoding of Decimal
    assert Decimal(str(body["total_cost_usd"])) == Decimal("0.006")
    assert body["total_tokens"] == 1500
    assert body["request_count"] == 3
    assert body["by_model"][0]["model"] == "claude-haiku-4-5"
    assert Decimal(str(body["by_model"][0]["cost_usd"])) == Decimal("0.006")
    assert body["by_day"][0]["day"] == "2026-06-01"
    assert body["by_day"][0]["tokens"] == 1500


async def test_usage_empty_workspace_is_zeroed(client: AsyncClient) -> None:
    ws = _ws()
    results = [_Result([(Decimal("0"), 0, 0)]), _Result([]), _Result([])]

    async def _dep():
        yield _UsageSession(ws, results)

    app.dependency_overrides[get_session] = _dep
    try:
        resp = await client.get("/v1/usage", headers={"Authorization": "Bearer k"})
    finally:
        app.dependency_overrides.clear()

    body = resp.json()
    assert Decimal(str(body["total_cost_usd"])) == Decimal("0")
    assert body["request_count"] == 0
    assert body["by_model"] == [] and body["by_day"] == []


# --------------------------------------------------------------------------- #
# GET /v1/provider/check — real BYOK-key validation (provider stubbed)
# --------------------------------------------------------------------------- #
import app.routers.meta as meta_mod  # noqa: E402


class _StubProvider:
    """Stands in for a real provider so validate() needs no network."""

    def __init__(self, exc: Exception | None = None) -> None:
        self._exc = exc

    async def validate(self) -> None:
        if self._exc is not None:
            raise self._exc


class _HttpErr(Exception):
    def __init__(self, code: int) -> None:
        self.status_code = code
        super().__init__(f"http {code}")


async def test_provider_check_requires_auth(client: AsyncClient, session_returns: Callable) -> None:
    session_returns(None)
    assert (await client.get("/v1/provider/check")).status_code == 401


async def test_provider_check_mock_when_no_byok_key(
    client: AsyncClient, session_returns: Callable
) -> None:
    session_returns(_ws())
    body = (await client.get("/v1/provider/check", headers={"Authorization": "Bearer k"})).json()
    assert body["ok"] is True and body["mode"] == "mock"


async def test_provider_check_unknown_provider_is_400(
    client: AsyncClient, session_returns: Callable
) -> None:
    session_returns(_ws())
    resp = await client.get(
        "/v1/provider/check",
        headers={"Authorization": "Bearer k", "X-LLM-Provider": "groq", "X-LLM-Api-Key": "x"},
    )
    assert resp.status_code == 400


async def test_provider_check_rejects_bad_key(
    client: AsyncClient, session_returns: Callable, monkeypatch
) -> None:
    session_returns(_ws())
    # A real authenticated call would 401 on a bogus key — the bug this fixes.
    monkeypatch.setattr(meta_mod, "get_provider_for", lambda n, k: _StubProvider(_HttpErr(401)))
    resp = await client.get(
        "/v1/provider/check",
        headers={"Authorization": "Bearer k", "X-LLM-Provider": "anthropic", "X-LLM-Api-Key": "bogus"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False and body["mode"] == "byok" and body["provider"] == "anthropic"


async def test_provider_check_accepts_valid_key(
    client: AsyncClient, session_returns: Callable, monkeypatch
) -> None:
    session_returns(_ws())
    monkeypatch.setattr(meta_mod, "get_provider_for", lambda n, k: _StubProvider())
    resp = await client.get(
        "/v1/provider/check",
        headers={"Authorization": "Bearer k", "X-LLM-Provider": "openai", "X-LLM-Api-Key": "sk-valid"},
    )
    assert resp.json()["ok"] is True


async def test_provider_check_rate_limited_is_still_valid(
    client: AsyncClient, session_returns: Callable, monkeypatch
) -> None:
    session_returns(_ws())
    # 429 means authenticated-but-throttled → the key itself is valid.
    monkeypatch.setattr(meta_mod, "get_provider_for", lambda n, k: _StubProvider(_HttpErr(429)))
    resp = await client.get(
        "/v1/provider/check",
        headers={"Authorization": "Bearer k", "X-LLM-Provider": "anthropic", "X-LLM-Api-Key": "k"},
    )
    assert resp.json()["ok"] is True

"""Meta / observability endpoints: health, workspace identity, tools, usage."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app.agent.providers import get_provider_for
from app.auth import CurrentWorkspace, DbSession
from app.db import engine
from app.observability.usage import aggregate_usage
from app.schemas.meta import ToolInfo, UsageSummaryOut
from app.tools import get_registry

router = APIRouter(tags=["meta"])


class HealthResponse(BaseModel):
    status: str


class WorkspaceIdentity(BaseModel):
    workspace_id: uuid.UUID
    name: str


@router.get("/healthz", response_model=HealthResponse)
async def healthz(response: Response) -> HealthResponse:
    """Liveness + DB connectivity check. Public (no auth)."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status="degraded")
    return HealthResponse(status="ok")


@router.get("/v1/me", response_model=WorkspaceIdentity, tags=["meta"])
async def whoami(workspace: CurrentWorkspace) -> WorkspaceIdentity:
    """Return the workspace resolved from the Bearer API key (auth smoke test)."""
    return WorkspaceIdentity(workspace_id=workspace.id, name=workspace.name)


class ProviderCheck(BaseModel):
    ok: bool
    mode: str  # "mock" | "byok"
    provider: str
    detail: str


@router.get("/v1/provider/check", response_model=ProviderCheck, tags=["meta"])
async def provider_check(
    workspace: CurrentWorkspace,
    x_llm_provider: Annotated[str | None, Header()] = None,
    x_llm_api_key: Annotated[str | None, Header()] = None,
) -> ProviderCheck:
    """Validate a BYOK provider key with a real, token-free call to the provider.

    Confirms the visitor's Anthropic/OpenAI key actually works (an invalid key is
    rejected with 401) — unlike /v1/me, which only checks the workspace key.
    Workspace auth is required so this can't be abused as an open key-testing
    oracle; the provider key is used transiently and never logged.
    """
    name = (x_llm_provider or "").strip().lower()
    key = (x_llm_api_key or "").strip()
    if not key or name in ("", "mock"):
        return ProviderCheck(
            ok=True, mode="mock", provider="mock",
            detail="Free mock provider — no external key to validate.",
        )
    if name not in ("anthropic", "openai"):
        raise HTTPException(status_code=400, detail=f"Unknown provider '{name}'.")

    try:
        await get_provider_for(name, key).validate()
    except Exception as exc:  # noqa: BLE001 — normalize SDK errors to a safe verdict
        code = getattr(exc, "status_code", None)
        if code in (401, 403):
            return ProviderCheck(
                ok=False, mode="byok", provider=name,
                detail=f"The {name} API rejected this key ({code}). Check the key and try again.",
            )
        if code == 429:  # authenticated but throttled → the key itself is valid
            return ProviderCheck(
                ok=True, mode="byok", provider=name,
                detail=f"Your {name} key is valid (currently rate-limited).",
            )
        if code is not None:
            return ProviderCheck(
                ok=False, mode="byok", provider=name,
                detail=f"The {name} API returned {code}. Try again shortly.",
            )
        return ProviderCheck(
            ok=False, mode="byok", provider=name,
            detail=f"Couldn't reach the {name} API. Check your network and try again.",
        )
    return ProviderCheck(ok=True, mode="byok", provider=name, detail=f"Your {name} key is valid.")


@router.get("/v1/tools", response_model=list[ToolInfo], tags=["meta"])
async def list_tools(workspace: CurrentWorkspace) -> list[dict]:
    """Everything the agent can do — name, description, and JSON-schema args.

    The registry is global (same for every workspace); auth is still required so
    the tool catalogue isn't exposed unauthenticated.
    """
    return get_registry().public_list()


@router.get("/v1/usage", response_model=UsageSummaryOut, tags=["meta"])
async def usage(workspace: CurrentWorkspace, session: DbSession) -> UsageSummaryOut:
    """Workspace-scoped cost/token/request totals with per-model and per-day rollups."""
    data = await aggregate_usage(session, workspace.id)
    return UsageSummaryOut.model_validate(data)

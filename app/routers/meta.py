"""Meta / observability endpoints: health, workspace identity, tools, usage."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy import text

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

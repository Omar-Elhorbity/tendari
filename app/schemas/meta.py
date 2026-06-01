"""Schemas for the meta / observability endpoints (GET /v1/tools, GET /v1/usage)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel


class ToolInfo(BaseModel):
    """One row of GET /v1/tools — what the agent can do, for Swagger/demo."""

    name: str
    description: str
    parameters_schema: dict[str, Any]


class UsageByModel(BaseModel):
    model: str
    cost_usd: Decimal
    tokens: int
    request_count: int


class UsageByDay(BaseModel):
    day: date
    cost_usd: Decimal
    tokens: int
    request_count: int


class UsageSummaryOut(BaseModel):
    """GET /v1/usage — workspace-scoped cost/token/request totals + rollups."""

    total_cost_usd: Decimal
    total_tokens: int
    request_count: int
    by_model: list[UsageByModel]
    by_day: list[UsageByDay]

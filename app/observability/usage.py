"""Per-LLM-call usage + cost recording.

Token prices change constantly, so they are CONFIG, never constants in logic:
the defaults below are overridable per-model via LLM_PRICING_JSON. Prices are
USD per 1,000 tokens. Update them to current provider pricing when you deploy.
"""

from __future__ import annotations

import logging
import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.providers.base import Usage
from app.config import settings
from app.models import UsageRecord

logger = logging.getLogger("tendari.usage")

# Default USD price per 1,000 tokens. Verify/adjust against current pricing.
_DEFAULT_PRICING: dict[str, dict[str, float]] = {
    "claude-haiku-4-5": {"input_per_1k": 0.001, "output_per_1k": 0.005},
    "claude-sonnet-4-6": {"input_per_1k": 0.003, "output_per_1k": 0.015},
    "claude-opus-4-8": {"input_per_1k": 0.015, "output_per_1k": 0.075},
    "gpt-4o": {"input_per_1k": 0.0025, "output_per_1k": 0.01},
    "gpt-4o-mini": {"input_per_1k": 0.00015, "output_per_1k": 0.0006},
}

_CENTS = Decimal("0.000001")


def _pricing_for(model: str) -> dict[str, float] | None:
    overrides = settings.pricing_overrides
    if model in overrides:
        return overrides[model]
    return _DEFAULT_PRICING.get(model)


def compute_cost_usd(usage: Usage) -> Decimal:
    """Cost for one call from token counts × per-model price. 0 for unknown models."""
    pricing = _pricing_for(usage.model)
    if pricing is None:
        logger.warning("No pricing for model %r; recording cost 0.", usage.model)
        return Decimal("0")
    cost = (
        Decimal(str(pricing.get("input_per_1k", 0))) * Decimal(usage.prompt_tokens) / 1000
        + Decimal(str(pricing.get("output_per_1k", 0))) * Decimal(usage.completion_tokens) / 1000
    )
    return cost.quantize(_CENTS, rounding=ROUND_HALF_UP)


async def record_usage(
    session_factory: async_sessionmaker,
    *,
    workspace_id: uuid.UUID,
    conversation_id: uuid.UUID | None,
    usage: Usage,
    latency_ms: int | None,
    endpoint: str | None,
) -> Decimal:
    """Record one LLM call's usage on its OWN committed session and return cost.

    Cost accounting is a durable audit: real spend already happened, so it must
    survive even if the surrounding request later rolls back (e.g. a provider
    error on a subsequent loop iteration). Hence an autonomous transaction rather
    than the request session.
    """
    cost = compute_cost_usd(usage)
    async with session_factory() as session:
        session.add(
            UsageRecord(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                model=usage.model,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                cost_usd=cost,
                latency_ms=latency_ms,
                endpoint=endpoint,
            )
        )
        await session.commit()
    return cost


# --------------------------------------------------------------------------- #
# aggregation — backs GET /v1/usage (always workspace-scoped)
# --------------------------------------------------------------------------- #
# Total tokens = prompt + completion, summed in SQL.
_TOKENS = UsageRecord.prompt_tokens + UsageRecord.completion_tokens


async def aggregate_usage(session: AsyncSession, workspace_id: uuid.UUID) -> dict:
    """Cost/token/request totals for a workspace, plus per-model and per-day rollups.

    Every aggregate is filtered by ``workspace_id`` — usage is tenant-private, so
    one workspace can never see another's spend. Empty workspace → zeros + [].
    """
    scope = UsageRecord.workspace_id == workspace_id

    totals = (
        await session.execute(
            select(
                func.coalesce(func.sum(UsageRecord.cost_usd), 0),
                func.coalesce(func.sum(_TOKENS), 0),
                func.count(),
            ).where(scope)
        )
    ).one()

    by_model = (
        await session.execute(
            select(
                UsageRecord.model,
                func.coalesce(func.sum(UsageRecord.cost_usd), 0),
                func.coalesce(func.sum(_TOKENS), 0),
                func.count(),
            )
            .where(scope)
            .group_by(UsageRecord.model)
            # cost desc, then model for a stable order on ties.
            .order_by(func.sum(UsageRecord.cost_usd).desc(), UsageRecord.model.asc())
        )
    ).all()

    # Bucket per calendar day in UTC. created_at is timestamptz; date() alone is
    # evaluated in the session timezone, so without the UTC cast day boundaries
    # would shift with the host's TZ. Convert to UTC wall-clock first.
    day = func.date(func.timezone("UTC", UsageRecord.created_at))
    by_day = (
        await session.execute(
            select(
                day.label("day"),
                func.coalesce(func.sum(UsageRecord.cost_usd), 0),
                func.coalesce(func.sum(_TOKENS), 0),
                func.count(),
            )
            .where(scope)
            .group_by(day)
            .order_by(day.desc())
        )
    ).all()

    return {
        "total_cost_usd": Decimal(str(totals[0])),
        "total_tokens": int(totals[1]),
        "request_count": int(totals[2]),
        "by_model": [
            {"model": r[0], "cost_usd": Decimal(str(r[1])), "tokens": int(r[2]), "request_count": int(r[3])}
            for r in by_model
        ],
        "by_day": [
            {"day": r[0], "cost_usd": Decimal(str(r[1])), "tokens": int(r[2]), "request_count": int(r[3])}
            for r in by_day
        ],
    }

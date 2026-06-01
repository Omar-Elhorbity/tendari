"""Idempotency claims — Redis-backed when reachable, in-process otherwise.

Used by side-effecting tools (e.g. send_email) to guarantee an action fires at
most once: claim a key with SET NX; if the claim fails, the action already
happened. On a failed side effect, release the key so a retry can proceed.

Redis makes the claim durable across restarts and shared across replicas. When
no key-value store is reachable (e.g. a minimal free deploy with only a web
service + database), it transparently falls back to a best-effort in-process
store — still correct within a single running instance, which is all the free
tier runs anyway. The fallback is latched on the first failed Redis call.
"""

from __future__ import annotations

import contextlib
import logging
import time
from functools import lru_cache

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger("tendari.cache")

_PREFIX = "tendari:idem:"
_DEFAULT_TTL_S = 24 * 60 * 60

# In-process fallback: key -> monotonic expiry. Used only after Redis is found
# unreachable. Best-effort and single-instance (lost on restart) — acceptable
# for at-most-once side effects on a single free-tier web instance.
_memory: dict[str, float] = {}
_use_memory = False


@lru_cache
def get_redis() -> "aioredis.Redis":
    return aioredis.from_url(settings.redis_url, decode_responses=True)


def _mem_claim(key: str, ttl_s: int) -> bool:
    now = time.monotonic()
    expiry = _memory.get(key)
    if expiry is not None and expiry > now:
        return False
    _memory[key] = now + ttl_s
    return True


async def claim_once(key: str, ttl_s: int = _DEFAULT_TTL_S) -> bool:
    """Return True if this is the first claim of ``key`` (caller may proceed)."""
    global _use_memory
    full = f"{_PREFIX}{key}"
    if _use_memory:
        return _mem_claim(full, ttl_s)
    try:
        return bool(await get_redis().set(full, "1", nx=True, ex=ttl_s))
    except Exception:
        _use_memory = True
        logger.warning("Redis unreachable; idempotency falling back to in-process store")
        return _mem_claim(full, ttl_s)


async def release(key: str) -> None:
    """Release a previously claimed key (e.g. after a failed side effect)."""
    full = f"{_PREFIX}{key}"
    if _use_memory:
        _memory.pop(full, None)
        return
    with contextlib.suppress(Exception):
        await get_redis().delete(full)

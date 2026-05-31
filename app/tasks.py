"""Celery application. Background tasks (document ingestion) register in M1."""

from __future__ import annotations

from celery import Celery

from app.config import settings

celery_app = Celery(
    "tendari",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# Ingestion tasks live in app.rag.ingest (added in M1) and are imported here so
# the worker registers them.
try:  # pragma: no cover - optional until M1
    from app.rag import ingest as _ingest  # noqa: F401
except ImportError:
    pass

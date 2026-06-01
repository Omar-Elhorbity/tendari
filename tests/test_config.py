"""Config: the database_url asyncpg-driver normaliser.

Managed hosts (Render/Railway/Heroku) inject driver-less Postgres URLs; the app
and Alembic use the async asyncpg driver, so the scheme must be normalised.
"""

from __future__ import annotations

import pytest

from app.config import Settings


@pytest.mark.parametrize(
    "given, expected",
    [
        # driver-less managed-host schemes get the asyncpg driver
        ("postgres://u:p@host:5432/db", "postgresql+asyncpg://u:p@host:5432/db"),
        ("postgresql://u:p@host/db", "postgresql+asyncpg://u:p@host/db"),
        # an explicit driver is left untouched (no double-prefix)
        ("postgresql+asyncpg://u:p@host/db", "postgresql+asyncpg://u:p@host/db"),
        ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        # query params (e.g. ?sslmode=require) ride along unchanged on the scheme rewrite
        (
            "postgres://u:p@host/db?sslmode=require",
            "postgresql+asyncpg://u:p@host/db?sslmode=require",
        ),
        # non-postgres schemes are not touched
        ("sqlite+aiosqlite:///x.db", "sqlite+aiosqlite:///x.db"),
    ],
)
def test_database_url_is_normalised_to_asyncpg(given: str, expected: str) -> None:
    assert Settings(database_url=given).database_url == expected


def test_default_database_url_already_asyncpg() -> None:
    # The built-in default must not be altered by the validator.
    assert Settings().database_url.startswith("postgresql+asyncpg://")

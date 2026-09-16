"""The application's engines connect inside DB_SCHEMA - measured on a real
connection, because the setting only matters if it reaches one."""

from collections.abc import Callable
from urllib.parse import unquote, urlsplit

import pytest
from sqlalchemy import text

from app.config import Settings
from app.database import build_async_engine, build_engine


@pytest.fixture
def settings_for(_postgres_url: str) -> Callable[..., Settings]:
    parts = urlsplit(_postgres_url)

    def make(schema: str, **extra: int) -> Settings:
        return Settings(
            db_host=parts.hostname or "localhost",
            db_port=parts.port or 5432,
            db_name=unquote(parts.path.lstrip("/")),
            db_user=unquote(parts.username or ""),
            db_password=unquote(parts.password or ""),
            db_schema=schema,
            **extra,
        )

    return make


def test_the_sync_engine_runs_in_the_schema(settings_for: Callable[..., Settings]) -> None:
    engine = build_engine(settings_for("ow_engine_probe", db_pool_size=3))
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SHOW search_path")).scalar() == "ow_engine_probe"
        assert engine.pool.size() == 3
    finally:
        engine.dispose()


async def test_the_async_engine_runs_in_the_schema(settings_for: Callable[..., Settings]) -> None:
    engine = build_async_engine(settings_for("ow_engine_probe"))
    try:
        async with engine.connect() as conn:
            assert (await conn.execute(text("SHOW search_path"))).scalar() == "ow_engine_probe"
    finally:
        await engine.dispose()


def test_public_keeps_the_servers_own_search_path(settings_for: Callable[..., Settings]) -> None:
    """The control: upstream's default passes no option, so the server's default
    applies - and it is not a schema we set."""
    engine = build_engine(settings_for("public"))
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SHOW search_path")).scalar() == '"$user", public'
    finally:
        engine.dispose()

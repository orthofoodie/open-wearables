"""The migrations, run as a role that owns one schema and nothing else.

That is how Open Wearables lives in a database it shares
(docs/deployment/shared-database.mdx): the role may create only inside its own
schema, so a migration that reaches outside it - or an alembic that looks for
its version table anywhere else - fails loudly instead of touching the host's
tables.

The role logs in with a password generated here, for a role that exists only in
the test database and is dropped when the test ends. It has to log in itself:
DB_SCHEMA is applied as a libpq `options` parameter, which replaces PGOPTIONS
rather than adding to it, so a `-c role=` there would be silently dropped.
"""

import os
import secrets
import subprocess
import sys
import uuid
from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest
from sqlalchemy import create_engine, text

BACKEND = Path(__file__).resolve().parents[2]

#: The newest revision the chain can be taken back to. Below it, upstream
#: downgrades drop unnamed constraints and fail - measured 2026-09-16: sixteen
#: reversible steps from a7c3e9f1b2d4, stopping at this one. Upstream's
#: migrations are not edited to move it (see AGENTS.md, "Database Migrations").
FLOOR = "cc39513098b0"


@pytest.fixture
def postgres_url(_postgres_url: str) -> str:
    return _postgres_url


@dataclass(frozen=True)
class SchemaRole:
    role: str
    password: str
    schema: str


@pytest.fixture
def schema_role(_postgres_url: str) -> Generator[SchemaRole, None, None]:
    # The schema is NOT named after the role: the default search_path starts
    # with "$user", which would find a same-named schema by itself and hide a
    # missing DB_SCHEMA.
    suffix = uuid.uuid4().hex[:8]
    # The password carries `@ / % #`: every migration below then proves that the
    # URL built from it (Settings.db_uri, then alembic's config) still reaches the
    # server with the password as written.
    found = SchemaRole(f"ow_mig_{suffix}", secrets.token_urlsafe(24) + "@/%#", f"ow_data_{suffix}")
    admin = create_engine(_postgres_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(
            text(f"CREATE ROLE \"{found.role}\" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '{found.password}'")
        )
        conn.execute(text(f'CREATE SCHEMA "{found.schema}" AUTHORIZATION "{found.role}"'))
    try:
        yield found
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{found.schema}" CASCADE'))
            conn.execute(text(f'DROP ROLE IF EXISTS "{found.role}"'))
        admin.dispose()


def _alembic(url: str, who: SchemaRole, schema: str, *args: str) -> subprocess.CompletedProcess[str]:
    parts = urlsplit(url)
    env = {
        **os.environ,
        "DB_HOST": parts.hostname or "localhost",
        "DB_PORT": str(parts.port or 5432),
        "DB_NAME": unquote(parts.path.lstrip("/")),
        "DB_USER": who.role,
        "DB_PASSWORD": who.password,
        "DB_SCHEMA": schema,
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _scalar(url: str, sql: str, **params: str) -> object:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            return conn.execute(text(sql), params).scalar()
    finally:
        engine.dispose()


def _head() -> str:
    out = subprocess.run(
        [sys.executable, "-m", "alembic", "heads"], cwd=BACKEND, capture_output=True, text=True, check=True
    ).stdout
    return out.split()[0]


def test_it_goes_up_down_to_the_floor_and_up_again_inside_its_schema(
    postgres_url: str, schema_role: SchemaRole
) -> None:
    version = f'SELECT version_num FROM "{schema_role.schema}".alembic_version'
    head = _head()

    up = _alembic(postgres_url, schema_role, schema_role.schema, "upgrade", "head")
    assert up.returncode == 0, up.stderr[-2000:]
    assert _scalar(postgres_url, version) == head
    # Everything it created is inside its schema, and it owns all of it.
    assert (
        _scalar(
            postgres_url,
            "SELECT count(*) FROM pg_tables WHERE tableowner = :r AND schemaname <> :s",
            r=schema_role.role,
            s=schema_role.schema,
        )
        == 0
    )
    assert (
        _scalar(postgres_url, "SELECT count(*) FROM pg_tables WHERE schemaname = :s", s=schema_role.schema) or 0
    ) > 0
    assert _scalar(postgres_url, "SELECT to_regclass('public.alembic_version')::text") is None

    down = _alembic(postgres_url, schema_role, schema_role.schema, "downgrade", FLOOR)
    assert down.returncode == 0, down.stderr[-2000:]
    assert _scalar(postgres_url, version) == FLOOR

    again = _alembic(postgres_url, schema_role, schema_role.schema, "upgrade", "head")
    assert again.returncode == 0, again.stderr[-2000:]
    assert _scalar(postgres_url, version) == head


def test_a_missed_db_schema_fails_loudly(postgres_url: str, schema_role: SchemaRole) -> None:
    """The control, and the reason the role matters: without DB_SCHEMA the
    connection falls back to `public`, where this role may create nothing - so the
    first thing alembic tries to write is refused, rather than written into the
    host's schema."""
    result = _alembic(postgres_url, schema_role, "public", "upgrade", "head")

    assert result.returncode != 0
    assert "permission denied" in result.stderr
    assert _scalar(postgres_url, "SELECT count(*) FROM pg_tables WHERE tableowner = :r", r=schema_role.role) == 0


#: The chain's first revision. Offline mode cannot reach head, upstream as much
#: as here: b2c3d4e5f6a1 inspects a live connection. The version table and the
#: schema statements are emitted before the first migration, so one is enough.
FIRST = "bb5425bd11d0"


def _offline(schema: str) -> str:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", FIRST, "--sql"],
        cwd=BACKEND,
        env={**os.environ, "DB_SCHEMA": schema},
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_the_offline_script_keeps_the_version_table_in_the_schema() -> None:
    """`version_table_schema` is the line that does not lean on the search path.
    Without it the online arms above still pass - the search path alone puts
    `alembic_version` in the schema - so this is the arm that reds."""
    sql = _offline("ow_offline")

    assert 'CREATE SCHEMA IF NOT EXISTS "ow_offline"' in sql
    assert 'SET search_path TO "ow_offline"' in sql
    assert "CREATE TABLE ow_offline.alembic_version" in sql
    assert "CREATE TABLE alembic_version" not in sql


def test_the_offline_script_for_public_is_upstreams() -> None:
    """The control: `public` emits no schema statement and an unqualified
    version table, exactly as before DB_SCHEMA existed."""
    sql = _offline("public")

    assert "CREATE TABLE alembic_version" in sql
    assert "search_path" not in sql
    assert "CREATE SCHEMA" not in sql

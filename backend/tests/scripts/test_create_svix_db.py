"""create_svix_db: a database of Open Wearables' own gets svix's database; a
shared one is left alone, because svix's is provisioned by the operator."""

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "init" / "create_svix_db.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("create_svix_db", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(schema: str) -> MagicMock:
    module = _load()
    connect = MagicMock()
    with (
        patch.object(module.psycopg, "connect", connect),
        patch.object(module.settings, "outgoing_webhooks_enabled", True),
        patch.object(module.settings, "db_schema", schema),
    ):
        module.create_svix_db()
    return connect


def test_a_shared_database_is_left_alone() -> None:
    assert not _run("openwearables").called


def test_its_own_database_still_gets_one() -> None:
    """The control: the skip is the schema's doing, not a script that never
    connects."""
    assert _run("public").called

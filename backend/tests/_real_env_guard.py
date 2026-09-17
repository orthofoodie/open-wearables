"""The test suite refuses to run beside a real `config/.env`.

`app.config` builds `Settings()` when it is imported (`settings = _get_settings()`),
and `Settings` reads `config/.env` as its `env_file`. The object is cached, so
the one built at the first `app` import is the one every test uses. In a
checkout that holds a real `config/.env` (a deployed stack's), collecting the
suite would load that deployment's passwords and client secrets into it.

The suite takes its settings from the environment block at the top of
`tests/conftest.py`, and never needs the file. So the rule is absence. Run the
suite from a fresh clone of this repository (git ignores `config/.env`) or in
CI. A clone writes nothing into this checkout. `git worktree add` would write
into its `.git`, which another user of a shared checkout also relies on.

Both checks run from `tests/__init__.py`. Python runs a package's `__init__`
before any module in it, conftest included, so they run before the first `app`
import. The second check makes that ordering hold in every run, CI included,
where the first check has no file to find.
"""

import sys
from collections.abc import Container
from pathlib import Path

#: The file `Settings.model_config["env_file"]` names: `backend/config/.env`.
REAL_ENV_FILE = Path(__file__).resolve().parents[1] / "config" / ".env"


def refuse_real_env_file(path: Path = REAL_ENV_FILE) -> None:
    """Raise if `path` exists; do nothing otherwise."""
    if path.exists():
        raise RuntimeError(
            f"refusing to run the test suite: {path} exists. Importing app.config "
            "builds Settings() with that file as its env_file, which would load a "
            "deployment's real secrets into the test process. Run the suite from a "
            "fresh clone (git clone; git ignores config/.env, and a clone writes "
            "nothing into this checkout) or in CI; it takes its settings from "
            "tests/conftest.py."
        )


def refuse_to_run_after_app(modules: Container[str] | None = None) -> None:
    """Raise if an `app` module is already imported: the check above came too late."""
    loaded = sys.modules if modules is None else modules
    early = [name for name in ("app", "app.config") if name in loaded]
    if early:
        raise RuntimeError(
            f"refusing to run the test suite: {', '.join(early)} was imported before "
            "tests/_real_env_guard.py ran, so Settings() may already hold values "
            "read from config/.env. The guard must run before the first app import."
        )

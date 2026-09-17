"""The suite refuses to run beside a real `config/.env` (`tests/_real_env_guard.py`).

Known-positives, because a guard that never fires looks exactly like a checkout
without the file:
- each check raises on its condition, and passes without it;
- the file check watches the file `Settings` actually reads;
- importing the tests package fires both. A copy of the package beside a fake
  `config/.env`, or imported after an `app` module, refuses to import; the same
  copy imports otherwise.
- the session's cached settings carry the conftest's test key, so the conftest's
  environment block ran before the first `app` import.

The real `config/` directory is never touched.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from app.config import Settings, settings
from tests._real_env_guard import REAL_ENV_FILE, refuse_real_env_file, refuse_to_run_after_app

TESTS = Path(__file__).resolve().parents[1]
FAKE = "SECRET_KEY=not-a-real-secret\n"


def test_an_existing_env_file_is_refused(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(FAKE)

    with pytest.raises(RuntimeError, match="refusing to run the test suite") as refused:
        refuse_real_env_file(env_file)

    # It recommends a clone, which writes nothing into this checkout, and no longer
    # the worktree 2825437 recommended. Naming a worktree to explain that is fine.
    assert "fresh clone (git clone" in str(refused.value)
    assert "git worktree add" not in str(refused.value)


def test_an_absent_env_file_is_fine(tmp_path: Path) -> None:
    assert refuse_real_env_file(tmp_path / ".env") is None


@pytest.mark.parametrize("loaded", [{"app"}, {"app.config"}, {"app", "app.config", "os"}])
def test_an_app_module_imported_first_is_refused(loaded: set[str]) -> None:
    with pytest.raises(RuntimeError, match="was imported before"):
        refuse_to_run_after_app(loaded)


@pytest.mark.parametrize("loaded", [set(), {"apple", "tests", "os"}])
def test_no_app_module_imported_is_fine(loaded: set[str]) -> None:
    assert refuse_to_run_after_app(loaded) is None


def test_the_guard_watches_the_file_settings_reads() -> None:
    assert Path(str(Settings.model_config["env_file"])).resolve() == REAL_ENV_FILE.resolve()


def test_the_session_settings_carry_the_conftest_key() -> None:
    """`settings` is built once, at the first `app` import. If the conftest set its
    environment after that import, the key here would be whatever the shell, or a
    config/.env, held."""
    assert settings.secret_key == os.environ["SECRET_KEY"]


def _package_copy(tmp_path: Path) -> Path:
    """The tests package's two guard files, laid out as in the checkout, beside an
    empty stand-in for the `app` package."""
    backend = tmp_path / "backend"
    (backend / "tests").mkdir(parents=True)
    for name in ("__init__.py", "_real_env_guard.py"):
        shutil.copy2(TESTS / name, backend / "tests" / name)
    (backend / "app").mkdir()
    (backend / "app" / "__init__.py").write_text("")
    (backend / "config").mkdir()
    return backend


def _run(backend: Path, statements: str) -> subprocess.CompletedProcess[str]:
    # -I ignores PYTHON* variables and the working directory; the copy goes first.
    script = f"import sys; sys.path.insert(0, sys.argv[1]); {statements}"
    return subprocess.run(
        [sys.executable, "-I", "-c", script, str(backend)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    ("env_file", "statements", "refusal"),
    [
        (True, "import tests", "config/.env exists"),
        (False, "import app; import tests", "app was imported before"),
        (False, "import tests; import app", None),
        (False, "import tests", None),
    ],
    ids=["env-file-present", "app-imported-first", "app-imported-after", "clean"],
)
def test_importing_the_tests_package(
    tmp_path: Path,
    env_file: bool,
    statements: str,
    refusal: str | None,
) -> None:
    backend = _package_copy(tmp_path)
    if env_file:
        (backend / "config" / ".env").write_text(FAKE)

    run = _run(backend, f"{statements}; print(tests.__file__)")

    if refusal is None:
        assert run.returncode == 0, run.stderr
        assert Path(run.stdout.strip()).resolve() == (backend / "tests" / "__init__.py").resolve()
    else:
        assert run.returncode != 0
        assert refusal in run.stderr

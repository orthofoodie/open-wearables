"""A settings error must not print the settings.

pydantic puts every value a `BaseSettings` class read from the environment, or
from `config/.env`, into a validation error's `input_value`, unless the class
sets `hide_input_in_errors`. The commonest boot failure, a deployment missing
`SECRET_KEY`, then writes `db_password`, `admin_password` and every provider
client secret to the container log.

The two settings objects this module builds pass `_env_file=None`, and the
secret is a canary set for the test alone. Importing `app.config` builds a
third, at module level, with the default env_file. So this module is hermetic
only because the suite refuses to run while `config/.env` exists
(`tests/_real_env_guard.py`), not because of anything it does itself.
"""

import pytest
from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings

from app.config import Settings

CANARY = "canary-db-password-5150"


@pytest.fixture
def secret_key_missing(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A secret in the environment, and the one required setting gone."""
    monkeypatch.setenv("DB_PASSWORD", CANARY)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    return monkeypatch


def test_settings_hides_its_input() -> None:
    assert Settings.model_config.get("hide_input_in_errors") is True


@pytest.mark.usefixtures("secret_key_missing")
def test_a_boot_without_secret_key_names_it_and_prints_no_secret() -> None:
    with pytest.raises(ValidationError) as refused:
        Settings(_env_file=None)

    text = str(refused.value)
    assert "secret_key" in text
    assert CANARY not in text
    assert "input_value" not in text


@pytest.mark.usefixtures("secret_key_missing")
def test_the_control_a_class_without_the_flag_prints_the_secret() -> None:
    """Without this arm, the one above could pass only because pydantic stopped
    printing inputs."""

    class Unprotected(BaseSettings):
        secret_key: str
        db_password: SecretStr = SecretStr("")

    with pytest.raises(ValidationError) as refused:
        Unprotected(_env_file=None)

    assert CANARY in str(refused.value)

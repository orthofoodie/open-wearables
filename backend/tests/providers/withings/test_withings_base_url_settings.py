"""The Withings hosts are settings, and every site reads them.

A test rig replays recorded Withings responses from a stub, which needs the data
API and the OAuth token endpoint to be configurable. The defaults must stay the
production host byte for byte, so an unset environment behaves exactly as the
hardcoded literals did. Each site is pinned separately: hardcoding any one of
them back makes its test fail.
"""

from typing import Any
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.config import Settings, settings
from app.services.providers.withings.handlers import rpc_client
from app.services.providers.withings.oauth import WithingsOAuth
from app.services.providers.withings.strategy import WithingsStrategy

UPSTREAM = "https://wbsapi.withings.net"
STUB = "http://withings-stub:8080"


def _oauth() -> WithingsOAuth:
    return WithingsOAuth(
        user_repo=MagicMock(),
        connection_repo=MagicMock(),
        provider_name="withings",
        api_base_url=UPSTREAM,
    )


def test_both_defaults_are_the_upstream_host_exactly() -> None:
    # Read from the field definitions, not from `settings`: a developer's
    # environment may set either variable, and the default is what ships.
    fields = Settings.model_fields
    assert fields["withings_api_base_url"].default == UPSTREAM
    assert fields["withings_oauth_base_url"].default == UPSTREAM


def test_the_token_url_is_unchanged_under_the_defaults() -> None:
    with patch.object(settings, "withings_oauth_base_url", UPSTREAM):
        assert _oauth().endpoints.token_url == f"{UPSTREAM}/v2/oauth2"


def test_the_strategy_reads_the_api_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "withings_api_base_url", STUB)
    assert WithingsStrategy().api_base_url == STUB


def test_the_token_endpoint_reads_the_oauth_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "withings_oauth_base_url", STUB)
    assert _oauth().endpoints.token_url == f"{STUB}/v2/oauth2"


def test_the_rpc_default_reads_the_api_setting_at_call_time(monkeypatch: pytest.MonkeyPatch) -> None:
    # Set AFTER import: a value bound at import time would not see this.
    monkeypatch.setattr(settings, "withings_api_base_url", STUB)
    captured: dict[str, Any] = {}

    def fake_request(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"status": 0, "body": {}}

    with patch.object(rpc_client, "make_authenticated_request", side_effect=fake_request):
        rpc_client.withings_request(
            db=MagicMock(),
            user_id=uuid4(),
            connection_repo=MagicMock(),
            oauth=MagicMock(),
            service_path="/measure",
            action="getmeas",
            params={},
        )

    assert captured["api_base_url"] == STUB


def test_moving_the_api_host_does_not_move_the_token_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    # Two settings on purpose: the token request carries the client secret, so
    # redirecting data calls must never redirect credentials along with them.
    monkeypatch.setattr(settings, "withings_api_base_url", STUB)
    monkeypatch.setattr(settings, "withings_oauth_base_url", UPSTREAM)

    assert WithingsStrategy().api_base_url == STUB
    assert _oauth().endpoints.token_url == f"{UPSTREAM}/v2/oauth2"

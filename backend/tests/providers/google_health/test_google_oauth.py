"""
Tests for the Google OAuth authorization request.

The Google Health API rejects any request whose access token carries a scope
outside what it allows: 403 ``DISALLOWED_OAUTH_SCOPES``, e.g.
``disallowed_scopes: people_user_gender_read``. With
``include_granted_scopes=true`` Google merges every scope the user ever granted
this OAuth client (such as People API ``user.gender.read`` from an older
consent) into the new token, so a connection that completes still cannot read
any data. The authorization request must ask for the configured scopes only.
"""

from urllib.parse import parse_qs, urlparse

from sqlalchemy.orm import Session

from app.config import settings
from app.services.providers.google_health.oauth import GoogleOAuth


def _auth_params() -> dict[str, list[str]]:
    from app.models import User
    from app.repositories.user_connection_repository import UserConnectionRepository
    from app.repositories.user_repository import UserRepository

    oauth = GoogleOAuth(
        user_repo=UserRepository(User),
        connection_repo=UserConnectionRepository(),
        provider_name="google",
        api_base_url="https://health.googleapis.com",
    )
    url, _ = oauth._build_auth_url("state-1")
    return parse_qs(urlparse(url).query)


class TestGoogleAuthorizationRequest:
    def test_does_not_merge_earlier_grants_into_the_token(self, db: Session) -> None:
        params = _auth_params()

        assert "include_granted_scopes" not in params

    def test_asks_for_exactly_the_configured_scopes(self, db: Session) -> None:
        params = _auth_params()

        assert params["scope"] == [settings.google_default_scope]
        assert not any("user.gender" in s or "user.birthday" in s for s in params["scope"][0].split())

    def test_still_requests_a_refresh_token(self, db: Session) -> None:
        params = _auth_params()

        assert params["access_type"] == ["offline"]
        assert params["prompt"] == ["consent"]


GOOGLEHEALTH = "https://www.googleapis.com/auth/googlehealth"


class TestGoogleScopesMatchWhatIsRead:
    """Ask Google only for what is ingested (Security, open-wearables#2).

    No nutrition scope: nothing downstream has a nutrition or hydration series,
    and hydration-log / nutrition-log both need it. No email: nothing reads the
    address, and openid already gives the stable subject.
    """

    def test_scope_is_openid_and_the_four_read_families(self, db: Session) -> None:
        assert set(settings.google_default_scope.split()) == {
            "openid",
            f"{GOOGLEHEALTH}.activity_and_fitness.readonly",
            f"{GOOGLEHEALTH}.health_metrics_and_measurements.readonly",
            f"{GOOGLEHEALTH}.sleep.readonly",
            f"{GOOGLEHEALTH}.settings.readonly",
        }

    def test_nothing_fetches_or_subscribes_to_nutrition_data(self, db: Session) -> None:
        from app.services.providers.google_health.metrics import METRICS
        from app.services.providers.google_health.webhook_service import GOOGLE_WEBHOOK_DATA_TYPES

        needs_nutrition_scope = {"hydration-log", "nutrition-log"}
        assert needs_nutrition_scope.isdisjoint(m.data_type for m in METRICS)
        assert needs_nutrition_scope.isdisjoint(GOOGLE_WEBHOOK_DATA_TYPES)

    def test_connect_resolves_only_the_health_user_id(self, db: Session) -> None:
        from unittest.mock import MagicMock, patch

        from app.models import User
        from app.repositories.user_connection_repository import UserConnectionRepository
        from app.repositories.user_repository import UserRepository
        from app.schemas.model_crud.credentials import OAuthTokenResponse

        oauth = GoogleOAuth(
            user_repo=UserRepository(User),
            connection_repo=UserConnectionRepository(),
            provider_name="google",
            api_base_url="https://health.googleapis.com",
        )
        answer = MagicMock()
        answer.json.return_value = {"healthUserId": "health-user-1"}
        with patch("app.services.providers.google_health.oauth.httpx.get", return_value=answer) as get:
            info = oauth._get_provider_user_info(
                OAuthTokenResponse(access_token="token", token_type="Bearer", expires_in=3600), "user-1"
            )

        assert info == {"user_id": "health-user-1", "username": None}
        assert [call.args[0] for call in get.call_args_list] == ["https://health.googleapis.com/v4/users/me/identity"]

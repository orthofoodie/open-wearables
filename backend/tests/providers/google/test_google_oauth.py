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
from app.services.providers.google.health_api.oauth import GoogleOAuth


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

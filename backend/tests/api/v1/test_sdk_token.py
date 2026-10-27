"""Tests for SDK token exchange endpoint."""

from uuid import uuid4

from jose import jwt
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.config import settings
from app.services import refresh_token_service
from tests.factories import ApplicationFactory, DeveloperFactory, UserFactory


class TestCreateUserToken:
    """Tests for POST /api/v1/users/{external_user_id}/token"""

    def test_create_token_success(self, client: TestClient, db: Session, api_v1_prefix: str) -> None:
        """Valid app credentials should return JWT token with refresh token."""
        developer = DeveloperFactory()
        # Create application with known secret (factory uses "hashed_test_app_secret" as hash)
        application = ApplicationFactory(developer=developer, app_secret="test_app_secret")
        user = UserFactory()  # Create real user for FK constraint

        response = client.post(
            f"{api_v1_prefix}/users/{user.id}/token",
            json={"app_id": application.app_id, "app_secret": "test_app_secret"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"
        assert data["expires_in"] == settings.access_token_expire_minutes * 60
        assert data["refresh_token"].startswith("rt-")

    def test_create_token_invalid_app_id(self, client: TestClient, db: Session, api_v1_prefix: str) -> None:
        """Non-existent app_id should return 401."""
        user = UserFactory()
        response = client.post(
            f"{api_v1_prefix}/users/{user.id}/token",
            json={"app_id": "nonexistent", "app_secret": "secret"},
        )
        assert response.status_code == 401

    def test_create_token_invalid_app_secret(self, client: TestClient, db: Session, api_v1_prefix: str) -> None:
        """Wrong app_secret should return 401."""
        developer = DeveloperFactory()
        application = ApplicationFactory(developer=developer, app_secret="real_secret")
        user = UserFactory()

        response = client.post(
            f"{api_v1_prefix}/users/{user.id}/token",
            json={"app_id": application.app_id, "app_secret": "wrong_secret"},
        )
        assert response.status_code == 401

    def test_token_contains_correct_claims(self, client: TestClient, db: Session, api_v1_prefix: str) -> None:
        """Token should contain scope=sdk, sub=user_id, app_id."""
        developer = DeveloperFactory()
        application = ApplicationFactory(developer=developer, app_secret="test_secret")
        user = UserFactory()

        response = client.post(
            f"{api_v1_prefix}/users/{user.id}/token",
            json={"app_id": application.app_id, "app_secret": "test_secret"},
        )

        assert response.status_code == 200
        token = response.json()["access_token"]
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm], options={"verify_exp": False})

        assert payload["sub"] == str(user.id)
        assert payload["scope"] == "sdk"
        assert payload["app_id"] == application.app_id
        assert "exp" in payload

    def test_create_token_missing_app_id(self, client: TestClient, db: Session, api_v1_prefix: str) -> None:
        """Missing app_id should return validation error."""
        user = UserFactory()
        response = client.post(
            f"{api_v1_prefix}/users/{user.id}/token",
            json={"app_secret": "secret"},
        )
        # FastAPI returns 422 for validation errors, but some configs return 400
        assert response.status_code in [400, 422]

    def test_create_token_missing_app_secret(self, client: TestClient, db: Session, api_v1_prefix: str) -> None:
        """Missing app_secret should return validation error."""
        user = UserFactory()
        response = client.post(
            f"{api_v1_prefix}/users/{user.id}/token",
            json={"app_id": "app_123"},
        )
        assert response.status_code in [400, 422]

    def test_create_token_empty_body(self, client: TestClient, db: Session, api_v1_prefix: str) -> None:
        """Empty body should return validation error."""
        user = UserFactory()
        response = client.post(
            f"{api_v1_prefix}/users/{user.id}/token",
            json={},
        )
        assert response.status_code in [400, 422]


class TestRevokeUserSdkTokens:
    """Tests for POST /api/v1/users/{user_id}/sdk-tokens/revoke (orthofoodie server#318).

    SDK refresh tokens never expire and rotate on every use, so the integrating
    server cannot hold the current one; this ends a user's SDK sessions by user id.
    """

    def _refreshes(self, client: TestClient, api_v1_prefix: str, token: str) -> int:
        return client.post(f"{api_v1_prefix}/token/refresh", json={"refresh_token": token}).status_code

    def test_revokes_every_sdk_token_of_the_user_and_no_one_else(
        self, client: TestClient, db: Session, api_v1_prefix: str, api_key_header: dict[str, str]
    ) -> None:
        user_a, user_b = UserFactory(), UserFactory()
        a_tokens = [refresh_token_service.create_sdk_refresh_token(db, user_a.id, "app") for _ in range(2)]
        b_token = refresh_token_service.create_sdk_refresh_token(db, user_b.id, "app")

        response = client.post(f"{api_v1_prefix}/users/{user_a.id}/sdk-tokens/revoke", headers=api_key_header)

        assert response.status_code == 200
        assert response.json() == {"revoked": 2}
        assert [self._refreshes(client, api_v1_prefix, t) for t in a_tokens] == [401, 401]
        # The control: another user's token still refreshes.
        assert self._refreshes(client, api_v1_prefix, b_token) == 200

    def test_refused_without_the_api_key(self, client: TestClient, db: Session, api_v1_prefix: str) -> None:
        user = UserFactory()
        token = refresh_token_service.create_sdk_refresh_token(db, user.id, "app")

        response = client.post(f"{api_v1_prefix}/users/{user.id}/sdk-tokens/revoke")

        assert response.status_code == 401
        assert self._refreshes(client, api_v1_prefix, token) == 200

    def test_an_unknown_user_revokes_nothing_and_is_not_a_404(
        self, client: TestClient, api_v1_prefix: str, api_key_header: dict[str, str]
    ) -> None:
        """200 with 0, so a caller can read a 404 as "this route is missing"."""
        response = client.post(f"{api_v1_prefix}/users/{uuid4()}/sdk-tokens/revoke", headers=api_key_header)

        assert response.status_code == 200
        assert response.json() == {"revoked": 0}

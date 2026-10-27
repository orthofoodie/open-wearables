"""The paths public on our load balancer refuse an oversized body with a 413,
before the body is parsed (orthofoodie/orthofoodie-infra#40, #45).

The load balancer has no body limit, and FastAPI parses a JSON body before the
auth dependency runs, so without this an unauthenticated caller could make the
API hold a body of any size. Each public path has its own bound:
- the phone SDK's uploads and the provider webhooks: `raw_payload_max_size_bytes`;
- the token refresh and the OAuth callbacks: 8 KB (a refresh is one token, and a
  callback carries its data in the query).
"""

from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from app.config import settings
from app.middlewares import PublicBodySizeLimit, public_body_limits

MAX = settings.raw_payload_max_size_bytes
SMALL = 8 * 1024
USER = "123e4567-e89b-12d3-a456-426614174000"
# In the direct tests the large bound is 10 bytes, so a body can cross it cheaply.
LARGE_PATHS = [
    f"/api/v1/sdk/users/{USER}/sync",
    f"/api/v1/sdk/users/{USER}/logs",
    "/api/v1/providers/garmin/webhooks",
    "/api/v1/providers/oura/webhooks/sleep",
]
SMALL_PATHS = ["/api/v1/token/refresh", "/api/v1/oauth/garmin/callback"]


def _chunks(total: int, size: int = 1024 * 1024) -> Iterator[bytes]:
    while total > 0:
        yield b"x" * min(size, total)
        total -= size


class _App:
    """Records whether the wrapped app ran."""

    def __init__(self) -> None:
        self.called = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.called = True
        while (await receive()).get("more_body"):
            pass
        await send({"type": "http.response.start", "status": 202, "headers": []})
        await send({"type": "http.response.body", "body": b""})


async def _run(path: str, headers: list[tuple[bytes, bytes]], messages: list[Message]) -> tuple[int, bool]:
    inner = _App()
    sent: list[Message] = []

    async def receive() -> Message:
        if not messages:
            raise AssertionError("the body was read")
        return messages.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {"type": "http", "method": "POST", "path": path, "headers": headers}
    await PublicBodySizeLimit(inner, limits=public_body_limits(10))(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, inner.called


@pytest.mark.parametrize(("path", "bound"), [(p, 10) for p in LARGE_PATHS] + [(p, SMALL) for p in SMALL_PATHS])
async def test_a_declared_length_over_its_bound_is_refused_before_any_byte_is_read(path: str, bound: int) -> None:
    status, called = await _run(path, [(b"content-length", str(bound + 1).encode())], [])
    assert status == 413
    assert not called


@pytest.mark.parametrize(("path", "bound"), [(p, 10) for p in LARGE_PATHS] + [(p, SMALL) for p in SMALL_PATHS])
async def test_a_body_at_its_bound_reaches_the_app(path: str, bound: int) -> None:
    status, called = await _run(
        path,
        [(b"content-length", str(bound).encode())],
        [{"type": "http.request", "body": b"x" * bound, "more_body": False}],
    )
    assert status == 202
    assert called


async def test_a_length_that_understates_the_body_is_caught_while_reading() -> None:
    status, _ = await _run(
        f"/api/v1/sdk/users/{USER}/sync",
        [(b"content-length", b"5")],
        [
            {"type": "http.request", "body": b"x" * 6, "more_body": True},
            {"type": "http.request", "body": b"x" * 6, "more_body": False},
        ],
    )
    assert status == 413


async def test_other_routes_are_not_capped() -> None:
    status, called = await _run(
        "/api/v1/users",
        [(b"content-length", str(MAX + 1).encode())],
        [{"type": "http.request", "body": b"x" * 11, "more_body": False}],
    )
    assert status == 202
    assert called


@pytest.mark.parametrize("route", ["sync", "logs"])
def test_a_chunked_sdk_body_over_the_cap_gets_413_from_the_app(client: TestClient, route: str) -> None:
    response = client.post(
        f"/api/v1/sdk/users/{USER}/{route}",
        content=_chunks(MAX + 1),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_an_sdk_body_at_the_cap_still_reaches_the_route(client: TestClient) -> None:
    """The control: exactly the cap is allowed through, and the route's own auth
    answers (no credentials here, so 401) - the limit is not what refused it."""
    head, tail = b'{"pad":"', b'"}'
    body = head + b"x" * (MAX - len(head) - len(tail)) + tail
    assert len(body) == MAX
    response = client.post(
        f"/api/v1/sdk/users/{USER}/sync",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 401


def test_a_chunked_refresh_over_8kb_gets_413_from_the_app(client: TestClient) -> None:
    response = client.post(
        "/api/v1/token/refresh",
        content=_chunks(SMALL + 1, size=1024),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_a_refresh_at_8kb_still_reaches_its_validation(client: TestClient) -> None:
    """The control: a refresh body of exactly 8 KB gets the refresh route's own
    answer to an unknown token, not the limit's."""
    head, tail = b'{"refresh_token":"', b'"}'
    body = head + b"x" * (SMALL - len(head) - len(tail)) + tail
    assert len(body) == SMALL
    response = client.post("/api/v1/token/refresh", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code not in (413, 500)
    assert 400 <= response.status_code < 500


def test_a_chunked_webhook_over_the_cap_gets_413_from_the_app(client: TestClient) -> None:
    response = client.post(
        "/api/v1/providers/garmin/webhooks",
        content=_chunks(MAX + 1),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_a_webhook_at_the_cap_still_reaches_its_handler(client: TestClient) -> None:
    """The control: a 10 MB webhook is answered by the webhook route, not the limit
    (Garmin's handler reads the body, then refuses a missing client id with 401)."""
    head, tail = b'{"pad":"', b'"}'
    body = head + b"x" * (MAX - len(head) - len(tail)) + tail
    assert len(body) == MAX
    response = client.post(
        "/api/v1/providers/garmin/webhooks", content=body, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 401

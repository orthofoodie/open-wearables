"""The phone SDK's upload routes refuse a body over `raw_payload_max_size_bytes`
with a 413, before the body is parsed (orthofoodie/orthofoodie-infra#40).

They are public on our load balancer, which has no body limit, and FastAPI parses
the JSON body before the auth dependency runs, so without this an unauthenticated
caller could make the API hold a body of any size.
"""

from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from app.config import settings
from app.middlewares import SdkBodySizeLimit

MAX = settings.raw_payload_max_size_bytes
USER = "123e4567-e89b-12d3-a456-426614174000"
SDK_ROUTES = ["sync", "logs"]


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


async def _run(
    middleware: SdkBodySizeLimit, path: str, headers: list[tuple[bytes, bytes]], messages: list[Message]
) -> int:
    sent: list[Message] = []

    async def receive() -> Message:
        if not messages:
            raise AssertionError("the body was read")
        return messages.pop(0)

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {"type": "http", "method": "POST", "path": path, "headers": headers}
    await middleware(scope, receive, send)
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


@pytest.mark.parametrize("route", SDK_ROUTES)
async def test_a_declared_length_over_the_cap_is_refused_before_any_byte_is_read(route: str) -> None:
    inner = _App()
    status = await _run(
        SdkBodySizeLimit(inner, max_bytes=10),
        f"/api/v1/sdk/users/{USER}/{route}",
        [(b"content-length", b"11")],
        [],  # receive() fails the test if it is called
    )
    assert status == 413
    assert not inner.called


async def test_a_length_that_understates_the_body_is_caught_while_reading() -> None:
    inner = _App()
    status = await _run(
        SdkBodySizeLimit(inner, max_bytes=10),
        f"/api/v1/sdk/users/{USER}/sync",
        [(b"content-length", b"5")],
        [
            {"type": "http.request", "body": b"x" * 6, "more_body": True},
            {"type": "http.request", "body": b"x" * 6, "more_body": False},
        ],
    )
    assert status == 413


async def test_other_routes_are_not_capped() -> None:
    inner = _App()
    status = await _run(
        SdkBodySizeLimit(inner, max_bytes=10),
        "/api/v1/users",
        [(b"content-length", b"11")],
        [{"type": "http.request", "body": b"x" * 11, "more_body": False}],
    )
    assert status == 202
    assert inner.called


@pytest.mark.parametrize("route", SDK_ROUTES)
def test_a_chunked_body_over_the_cap_gets_413_from_the_app(client: TestClient, route: str) -> None:
    response = client.post(
        f"/api/v1/sdk/users/{USER}/{route}",
        content=_chunks(MAX + 1),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_a_body_at_the_cap_still_reaches_the_route(client: TestClient) -> None:
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

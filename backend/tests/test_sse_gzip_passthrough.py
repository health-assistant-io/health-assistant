"""SSE transport hygiene (R7 WS7 — F5 + F14), pinned on a minimal ASGI app
(no DB) carrying the production gzip middleware:

* ``text/event-stream`` responses are NEVER gzip-compressed under
  ``Accept-Encoding: gzip`` while a >1000-byte JSON response still is;
* SSE frames are forwarded incrementally — the first chunk is observable
  as its own ASGI body message before the stream ends;
* every SSE response carries ``Cache-Control: no-cache`` and
  ``X-Accel-Buffering: no``;
* the shared keep-alive wrapper emits ``: keep-alive`` comment lines on
  idle iterators (short interval injected) and never drops or reorders a
  frame — including frames completing exactly at a heartbeat boundary.
"""

import asyncio
import gzip
import json

import pytest
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from app.ai.assistance import turns
from app.ai.assistance.turns import SSE_RESPONSE_HEADERS, sse_keep_alive
from app.main import SSEPassthroughGZipMiddleware


def _build_app(slow: bool = False) -> Starlette:
    """Minimal app with the production gzip middleware + the production
    SSE response construction (media type + shared headers + keep-alive)."""

    async def frames():
        yield 'data: {"n": 1}\n\n'
        if slow:
            await asyncio.sleep(0.13)
        else:
            await asyncio.sleep(0.01)
        yield 'data: {"n": 2}\n\n'
        yield 'data: {"n": 3}\n\n'

    async def sse(_request):
        return StreamingResponse(
            sse_keep_alive(frames()),
            media_type="text/event-stream",
            headers=SSE_RESPONSE_HEADERS,
        )

    async def big_json(_request):
        return JSONResponse({"blob": "x" * 2000})

    return Starlette(
        routes=[Route("/sse", sse), Route("/big-json", big_json)],
        middleware=[Middleware(SSEPassthroughGZipMiddleware, minimum_size=1000)],
    )


async def _drive(app, path: str, headers: dict[str, str] | None = None) -> list[dict]:
    """Run one request through the ASGI stack, collecting every sent message.

    ``spec_version`` 2.4 keeps StreamingResponse from consuming ``receive``
    (no disconnect listener), so the driver stays a pure observer."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "server": ("testserver", 80),
        "client": ("test", 123),
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(dict(message))

    messages: list[dict] = []
    await app(scope, receive, send)
    return messages


def _start_message(messages: list[dict]) -> dict:
    return next(m for m in messages if m["type"] == "http.response.start")


def _headers(start: dict) -> dict[str, str]:
    return {k.decode().lower(): v.decode() for k, v in start["headers"]}


def _body_messages(messages: list[dict]) -> list[dict]:
    return [m for m in messages if m["type"] == "http.response.body"]


def _joined_body(messages: list[dict]) -> bytes:
    return b"".join(m.get("body", b"") for m in _body_messages(messages))


@pytest.mark.asyncio
async def test_event_stream_bypasses_gzip_while_large_json_is_compressed():
    app = _build_app()

    sse_messages = await _drive(app, "/sse", {"Accept-Encoding": "gzip"})
    sse_headers = _headers(_start_message(sse_messages))
    assert sse_headers["content-type"].startswith("text/event-stream")
    assert "content-encoding" not in sse_headers
    assert _joined_body(sse_messages) == (b'data: {"n": 1}\n\ndata: {"n": 2}\n\ndata: {"n": 3}\n\n')

    json_messages = await _drive(app, "/big-json", {"Accept-Encoding": "gzip"})
    json_headers = _headers(_start_message(json_messages))
    assert json_headers.get("content-encoding") == "gzip"
    payload = json.loads(gzip.decompress(_joined_body(json_messages)))
    assert payload == {"blob": "x" * 2000}


@pytest.mark.asyncio
async def test_sse_frames_arrive_incrementally():
    app = _build_app()
    messages = await _drive(app, "/sse", {"Accept-Encoding": "gzip"})
    bodies = _body_messages(messages)
    # Three data frames forwarded chunk-by-chunk + the terminal empty body —
    # a middleware that buffered to stream end would deliver exactly one.
    assert len(bodies) >= 4
    assert bodies[0]["body"] == b'data: {"n": 1}\n\n'
    assert bodies[0]["more_body"] is True
    assert b'data: {"n": 2}' in bodies[1]["body"]
    assert bodies[-1]["more_body"] is False
    assert bodies[-1]["body"] == b""


@pytest.mark.asyncio
async def test_sse_headers_present_without_gzip_negotiation():
    app = _build_app()
    messages = await _drive(app, "/sse")
    headers = _headers(_start_message(messages))
    assert headers["cache-control"] == "no-cache"
    assert headers["x-accel-buffering"] == "no"
    assert "content-encoding" not in headers


@pytest.mark.asyncio
async def test_keep_alive_comments_on_slow_iterator(monkeypatch):
    monkeypatch.setattr(turns, "SSE_HEARTBEAT_SECONDS", 0.05)

    async def slow():
        yield "data: 1\n\n"
        await asyncio.sleep(0.13)
        yield "data: 2\n\n"

    frames = [frame async for frame in sse_keep_alive(slow())]
    assert frames[0] == "data: 1\n\n"
    assert frames[-1] == "data: 2\n\n"
    comments = frames[1:-1]
    assert comments
    assert all(frame == ": keep-alive\n\n" for frame in comments)


@pytest.mark.asyncio
async def test_keep_alive_never_drops_frames_at_timeout_boundary(monkeypatch):
    """Zero interval: every wait races the frame — none may be lost or
    reordered (the __anext__ task is re-awaited, never cancelled)."""
    monkeypatch.setattr(turns, "SSE_HEARTBEAT_SECONDS", 0.0)

    async def burst():
        for i in range(20):
            await asyncio.sleep(0.001)
            yield f"data: {i}\n\n"

    frames = [frame async for frame in sse_keep_alive(burst())]
    data = [frame for frame in frames if frame.startswith("data:")]
    assert data == [f"data: {i}\n\n" for i in range(20)]


@pytest.mark.asyncio
async def test_keep_alive_comments_flow_through_the_stack(monkeypatch):
    monkeypatch.setattr(turns, "SSE_HEARTBEAT_SECONDS", 0.05)
    app = _build_app(slow=True)

    messages = await _drive(app, "/sse", {"Accept-Encoding": "gzip"})
    parts = [part for part in _joined_body(messages).split(b"\n\n") if part]
    assert b'data: {"n": 1}' in parts[0]
    first_comment = parts.index(b": keep-alive")
    assert first_comment > 0
    assert parts.index(b'data: {"n": 2}') > first_comment
    assert all(part.startswith((b"data:", b":")) for part in parts)

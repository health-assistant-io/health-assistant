"""Tests for the streaming-error classifier in the ai-assistance endpoint.

Guarantees that provider/LLM exceptions are mapped to stable, non-leaky codes
(so raw SDK text like OpenAI's "Connection error." never reaches the client)
and that soft ValueError guard messages are forwarded verbatim.
"""
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    RateLimitError,
)

from app.api.v1.endpoints.ai_assistance import _classify_stream_error


class TestClassifyStreamError:
    def test_value_error_is_guard_with_message(self):
        # ValueError is the channel for intentional client-facing guards
        # (ownership/no-tasks/pending); its message must be forwarded as-is.
        etype, msg = _classify_stream_error(
            ValueError("Session not found or access denied.")
        )
        assert etype == "guard"
        assert msg == "Session not found or access denied."

    def test_connection_error_classified_without_leaking(self):
        exc = APIConnectionError(request=None)
        etype, msg = _classify_stream_error(exc)
        assert etype == "connection"
        # No raw SDK text leaks — frontend localizes from the code.
        assert msg == ""

    def test_timeout_error_classified(self):
        exc = APITimeoutError(request=None)
        etype, _ = _classify_stream_error(exc)
        assert etype == "timeout"

    def test_auth_error_classified(self):
        # AuthenticationError requires a response; build a minimal stub.
        import httpx

        response = httpx.Response(
            status_code=401,
            request=httpx.Request("POST", "https://api.openai.com/v1"),
        )
        exc = AuthenticationError(message="auth failed", response=response, body=None)
        etype, msg = _classify_stream_error(exc)
        assert etype == "auth"
        assert msg == ""

    def test_rate_limit_classified(self):
        import httpx

        response = httpx.Response(
            status_code=429,
            request=httpx.Request("POST", "https://api.openai.com/v1"),
        )
        exc = RateLimitError(message="slow down", response=response, body=None)
        etype, _ = _classify_stream_error(exc)
        assert etype == "rate_limit"

    def test_generic_fallback_for_unknown_exception(self):
        # Anything else (including other APIStatusError subclasses not
        # explicitly mapped) degrades to "generic" without leaking text.
        etype, msg = _classify_stream_error(RuntimeError("something broke"))
        assert etype == "generic"
        assert msg == ""

    def test_generic_message_is_never_raw_sdk_text(self):
        # Even a noisy exception with provider URLs must NOT leak its text.
        etype, msg = _classify_stream_error(
            RuntimeError("connect to https://api.openai.com failed: timeout")
        )
        assert msg == ""
        assert etype == "generic"

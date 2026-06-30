"""Regression tests for Web Push dead-subscription cleanup (C13).

Pre-fix contract: ``webpush_service.send_web_push`` swallowed every
``WebPushException`` including the 410 Gone / 404 Not Found cases that
RFC 8030 specifies as "subscription permanently dead". Returned ``False``.
The caller couldn't distinguish transient failure from permanent death,
so dead subscription rows in ``notification_subscriptions`` stayed
``is_active = True`` forever — every PUSH cycle re-attempted them and
re-paid the HTTP cost of re-discovering they were still dead.

Post-fix contract pinned here:
1. ``send_web_push`` raises ``SubscriptionExpired`` on HTTP 410/404.
   The exception carries the endpoint and status code for telemetry.
2. ``send_web_push`` still returns ``False`` for transient failures
   (5xx, network errors, etc.) — those may succeed on retry.
3. ``deliver_notification`` (the only caller) catches
   ``SubscriptionExpired``, marks the matching
   ``NotificationSubscription.is_active = False``, and continues
   processing the remaining subscriptions.
"""
import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pywebpush import WebPushException

from app.services.webpush_service import send_web_push, SubscriptionExpired


def _make_webpush_exception(status_code: int) -> WebPushException:
    """Build a WebPushException carrying a fake response with the given status."""
    fake_response = MagicMock()
    fake_response.status_code = status_code
    exc = WebPushException("fake")
    exc.response = fake_response
    return exc


# ---------------------------------------------------------------------------
# C13 part 1: send_web_push raises SubscriptionExpired on 410/404
# ---------------------------------------------------------------------------


def test_c13_send_web_push_raises_subscription_expired_on_410():
    """HTTP 410 Gone = subscription permanently dead → SubscriptionExpired."""
    sub_info = {"endpoint": "https://push.example.com/abc"}
    with patch("app.services.webpush_service.webpush", side_effect=_make_webpush_exception(410)):
        with patch("app.services.webpush_service.VAPID_PRIVATE_KEY", "fake-key"):
            with pytest.raises(SubscriptionExpired) as exc_info:
                send_web_push(sub_info, {"title": "hi"})
    assert exc_info.value.status_code == 410
    assert "https://push.example.com/abc" in str(exc_info.value)


def test_c13_send_web_push_raises_subscription_expired_on_404():
    """HTTP 404 Not Found = subscription revoked → SubscriptionExpired."""
    sub_info = {"endpoint": "https://push.example.com/xyz"}
    with patch("app.services.webpush_service.webpush", side_effect=_make_webpush_exception(404)):
        with patch("app.services.webpush_service.VAPID_PRIVATE_KEY", "fake-key"):
            with pytest.raises(SubscriptionExpired) as exc_info:
                send_web_push(sub_info, {"title": "hi"})
    assert exc_info.value.status_code == 404


def test_c13_send_web_push_returns_false_on_transient_failure():
    """HTTP 5xx / other WebPushException = transient → return False (retry possible)."""
    sub_info = {"endpoint": "https://push.example.com/abc"}
    with patch("app.services.webpush_service.webpush", side_effect=_make_webpush_exception(503)):
        with patch("app.services.webpush_service.VAPID_PRIVATE_KEY", "fake-key"):
            result = send_web_push(sub_info, {"title": "hi"})
    assert result is False


def test_c13_send_web_push_returns_true_on_success():
    """Successful delivery → True (unchanged behaviour)."""
    sub_info = {"endpoint": "https://push.example.com/abc"}
    fake_response = MagicMock()
    fake_response.ok = True
    fake_response.status_code = 201
    with patch("app.services.webpush_service.webpush", return_value=fake_response):
        with patch("app.services.webpush_service.VAPID_PRIVATE_KEY", "fake-key"):
            result = send_web_push(sub_info, {"title": "hi"})
    assert result is True


# ---------------------------------------------------------------------------
# C13 part 2: deliver_notification deactivates dead subscriptions
# ---------------------------------------------------------------------------


def test_c13_deliver_notification_handles_subscription_expired():
    """The deliver_notification PUSH loop must:
    1. SELECT subscription id alongside subscription_data (so it can target
       the row for deactivation).
    2. Catch SubscriptionExpired and UPDATE the row to is_active = False.
    """
    from app.workers import tasks as worker_tasks

    src = inspect.getsource(worker_tasks.deliver_notification)
    # Must select the id, not just subscription_data.
    assert "NotificationSubscription.id" in src, (
        "deliver_notification must SELECT NotificationSubscription.id alongside "
        "subscription_data so dead subscriptions can be deactivated by row id."
    )
    # Must catch SubscriptionExpired.
    assert "SubscriptionExpired" in src, (
        "deliver_notification must catch SubscriptionExpired from send_web_push."
    )
    # Must UPDATE is_active = False on the dead subscription.
    assert "is_active" in src and "False" in src, (
        "deliver_notification must set is_active = False on dead subscriptions."
    )

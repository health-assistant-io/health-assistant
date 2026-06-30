import logging
from urllib.parse import urlparse
from pywebpush import webpush, WebPushException
from app.core.config import settings
import json

logger = logging.getLogger(__name__)


def _redact_endpoint(endpoint: str) -> str:
    """Reduce a Web Push subscription endpoint to its origin for logging.

    Push subscription URLs embed a long-lived secret token in the path
    (e.g. ``https://fcm.googleapis.com/fcm/send/<SECRET>``). Anyone who
    reads that token can push messages to — or track — the subscriber,
    so the full endpoint must never appear in logs.
    """
    try:
        parsed = urlparse(endpoint)
        return f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else "unknown"
    except Exception:
        return "unknown"

# Note: In a real production app, these should be in settings/env
# For this project, we can generate them if they don't exist or use placeholders
VAPID_PRIVATE_KEY = getattr(settings, "VAPID_PRIVATE_KEY", None)
VAPID_PUBLIC_KEY = getattr(settings, "VAPID_PUBLIC_KEY", None)
VAPID_ADMIN_EMAIL = getattr(settings, "VAPID_ADMIN_EMAIL", "admin@health-assistant.local")
VAPID_CLAIMS = {"sub": f"mailto:{VAPID_ADMIN_EMAIL}"}


class SubscriptionExpired(Exception):
    """Raised when a Web Push endpoint returns 410 Gone or 404 Not Found.

    Per RFC 8291 / RFC 8030, these status codes mean the push subscription
    is permanently dead (user revoked permission, subscription expired,
    or the push service deleted it). The caller should mark the
    subscription row ``is_active = False`` so the DB doesn't keep polling
    a dead endpoint forever.
    """

    def __init__(self, endpoint: str, status_code: int):
        self.endpoint = endpoint
        self.status_code = status_code
        super().__init__(
            f"Web Push subscription {endpoint} expired (HTTP {status_code})"
        )


def send_web_push(subscription_info, data):
    """
    Sends a web push notification.

    :param subscription_info: The subscription info from the browser
    :param data: The payload (dict or string)
    :returns: True if delivered, False on transient failure.
    :raises SubscriptionExpired: if the endpoint returns 410 Gone or 404 Not
        Found. The caller MUST deactivate the subscription row in the DB.
    """
    if not VAPID_PRIVATE_KEY:
        logger.warning("VAPID_PRIVATE_KEY not configured. Web Push skipped.")
        return False

    endpoint = subscription_info.get("endpoint", "unknown")

    try:
        logger.info(f"Attempting to send Web Push to endpoint: {_redact_endpoint(endpoint)}")
        response = webpush(
            subscription_info=subscription_info,
            data=json.dumps(data) if isinstance(data, dict) else data,
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS,
        )
        logger.info(f"Web Push response status: {response.status_code}")
        return response.ok
    except WebPushException as ex:
        status_code = ex.response.status_code if ex.response else None
        logger.error(
            f"Web Push to {_redact_endpoint(endpoint)} failed (status {status_code}): {ex.message}"
        )
        # 410 Gone / 404 Not Found: the subscription is permanently dead.
        # Signal the caller to deactivate the row in the DB so we stop
        # polling a dead endpoint every notification cycle.
        if status_code in (404, 410):
            raise SubscriptionExpired(endpoint, status_code) from ex
        return False
    except Exception as e:
        logger.error("Unexpected error in Web Push: %s", e)
        return False

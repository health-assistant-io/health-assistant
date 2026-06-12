import logging
from pywebpush import webpush, WebPushException
from app.core.config import settings
import json

logger = logging.getLogger(__name__)

# Note: In a real production app, these should be in settings/env
# For this project, we can generate them if they don't exist or use placeholders
VAPID_PRIVATE_KEY = getattr(settings, "VAPID_PRIVATE_KEY", None)
VAPID_PUBLIC_KEY = getattr(settings, "VAPID_PUBLIC_KEY", None)
VAPID_ADMIN_EMAIL = getattr(settings, "VAPID_ADMIN_EMAIL", "admin@health-assistant.local")
VAPID_CLAIMS = {"sub": f"mailto:{VAPID_ADMIN_EMAIL}"}


def send_web_push(subscription_info, data):
    """
    Sends a web push notification.
    :param subscription_info: The subscription info from the browser
    :param data: The payload (dict or string)
    """
    if not VAPID_PRIVATE_KEY:
        logger.warning("VAPID_PRIVATE_KEY not configured. Web Push skipped.")
        return False

    try:
        logger.info(
            f"Attempting to send Web Push to endpoint: {subscription_info.get('endpoint')}"
        )
        response = webpush(
            subscription_info=subscription_info,
            data=json.dumps(data) if isinstance(data, dict) else data,
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS,
        )
        logger.info(f"Web Push response status: {response.status_code}")
        return response.ok
    except WebPushException as ex:
        logger.error(
            f"Web Push failed with status {ex.response.status_code if ex.response else 'unknown'}: {ex.message}"
        )
        # If the error is 410 (Gone) or 404 (Not Found), the subscription is expired/invalid
        if ex.response and ex.response.status_code in [404, 410]:
            # Should handle subscription cleanup here or return specific error
            pass
        return False
    except Exception as e:
        logger.error("Unexpected error in Web Push: %s", e)
        return False

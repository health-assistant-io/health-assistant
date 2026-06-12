import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging

logger = logging.getLogger(__name__)


class NotificationService:
    """Handles email and push notifications"""

    def __init__(self, settings):
        self.settings = settings
        self.smtp_config = (
            {
                "host": settings.SMTP_HOST,
                "port": settings.SMTP_PORT,
                "user": settings.SMTP_USER,
                "password": settings.SMTP_PASSWORD,
                "from": settings.SMTP_FROM,
            }
            if settings.SMTP_HOST
            else None
        )

    async def send_email(self, to: str, subject: str, body: str) -> bool:
        """Send email notification"""
        if not self.smtp_config:
            return False

        try:
            msg = MIMEMultipart()
            msg["From"] = self.smtp_config["from"]
            msg["To"] = to
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "html"))

            with smtplib.SMTP(
                self.smtp_config["host"], self.smtp_config["port"]
            ) as server:
                server.starttls()
                server.login(self.smtp_config["user"], self.smtp_config["password"])
                server.send_message(msg)

            return True
        except Exception as e:
            logger.error("Failed to send email: %s", e)
            return False

    def send_sms(self, phone: str, message: str) -> bool:
        """Send SMS notification (placeholder)"""
        # Implement SMS gateway integration
        return True

    def send_push(self, device_token: str, title: str, body: str) -> bool:
        """Send push notification (placeholder)"""
        # Implement push notification service
        return True

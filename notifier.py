"""
WhatsApp Notification Manager
Supports CallMeBot (free personal WhatsApp gateway) and Twilio WhatsApp.
"""

import logging
import urllib.parse
from typing import Dict, Any, Optional
import requests

logger = logging.getLogger(__name__)


class WhatsAppNotifier:
    """Sends notifications via WhatsApp."""

    def __init__(self, config: Dict[str, Any]):
        self.enabled = config.get("enabled", False)
        self.provider = config.get("provider", "callmebot").lower()
        self.callmebot_cfg = config.get("callmebot", {})
        self.twilio_cfg = config.get("twilio", {})

    def send(self, message: str) -> bool:
        """Send a WhatsApp message if enabled."""
        if not self.enabled:
            logger.debug("WhatsApp notifications disabled. Message: %s", message)
            return False

        try:
            if self.provider == "callmebot":
                return self._send_callmebot(message)
            elif self.provider == "twilio":
                return self._send_twilio(message)
            else:
                logger.warning("Unsupported WhatsApp provider: %s", self.provider)
                return False
        except Exception as e:
            logger.error("Failed to send WhatsApp notification: %s", e)
            return False

    def _send_callmebot(self, message: str) -> bool:
        phone = self.callmebot_cfg.get("phone")
        apikey = self.callmebot_cfg.get("apikey")

        if not phone or not apikey or apikey == "YOUR_CALLMEBOT_API_KEY":
            logger.warning("CallMeBot phone or API key not configured properly.")
            return False

        # Clean phone format: remove spaces and hyphens
        phone_cleaned = str(phone).replace(" ", "").replace("-", "")

        url = "https://api.callmebot.com/whatsapp.php"
        params = {
            "phone": phone_cleaned,
            "text": message,
            "apikey": apikey
        }

        resp = requests.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            logger.info("WhatsApp notification sent via CallMeBot.")
            return True
        else:
            logger.warning("CallMeBot returned status %d: %s", resp.status_code, resp.text)
            return False

    def _send_twilio(self, message: str) -> bool:
        account_sid = self.twilio_cfg.get("account_sid")
        auth_token = self.twilio_cfg.get("auth_token")
        from_num = self.twilio_cfg.get("from_number")
        to_num = self.twilio_cfg.get("to_number")

        if not (account_sid and auth_token and from_num and to_num):
            logger.warning("Twilio credentials not configured completely.")
            return False

        url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
        data = {
            "From": from_num,
            "To": to_num,
            "Body": message
        }

        resp = requests.post(url, data=data, auth=(account_sid, auth_token), timeout=10)
        if 200 <= resp.status_code < 300:
            logger.info("WhatsApp notification sent via Twilio.")
            return True
        else:
            logger.warning("Twilio returned status %d: %s", resp.status_code, resp.text)
            return False

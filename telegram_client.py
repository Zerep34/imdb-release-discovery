"""Send messages to a Telegram channel (retries, backoff, 429 handling)."""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)

TIMEOUT = 15
MAX_RETRIES = 4


class TelegramError(Exception):
    """Telegram network or API error."""


class TelegramClient:
    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base = f"https://api.telegram.org/bot{bot_token}"
        self.session = requests.Session()

    def _post(self, method: str, payload: dict) -> dict:
        url = f"{self.base}/{method}"
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.post(url, json=payload, timeout=TIMEOUT)
            except requests.RequestException as exc:
                if attempt == MAX_RETRIES:
                    raise TelegramError(f"Network failure ({method}): {exc}") from exc
                time.sleep(min(2 ** attempt, 10))
                continue

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "1"))
                data = resp.json() if resp.content else {}
                wait = int(data.get("parameters", {}).get("retry_after", wait))
                log.warning("Telegram 429, waiting %ss", wait)
                time.sleep(wait)
                continue
            data = resp.json() if resp.content else {}
            if resp.status_code == 401:
                raise TelegramError("Invalid Telegram bot token (401).")
            if not resp.ok or not data.get("ok", False):
                desc = data.get("description", resp.text[:200])
                if resp.status_code >= 500 and attempt < MAX_RETRIES:
                    time.sleep(min(2 ** attempt, 10))
                    continue
                raise TelegramError(f"Telegram {method} failed: {desc}")
            return data["result"]
        raise TelegramError(f"Aborted after {MAX_RETRIES} attempts ({method})")

    def get_me(self) -> dict:
        """Validate the token (--check)."""
        return self._post("getMe", {})

    @staticmethod
    def _markup(buttons: list[dict] | None) -> dict | None:
        """Build an inline_keyboard from [{text, url}] data: 2 buttons per row."""
        if not buttons:
            return None
        rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
        return {"inline_keyboard": rows}

    def send_message(self, text: str, disable_preview: bool = True,
                     buttons: list[dict] | None = None) -> dict:
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": disable_preview,
        }
        markup = self._markup(buttons)
        if markup:
            payload["reply_markup"] = markup
        return self._post("sendMessage", payload)

    def send_all(self, messages: list[str]) -> None:
        for msg in messages:
            self.send_message(msg)

    def send_plan(self, plan: list[dict]) -> None:
        """Execute a text-message plan (card mode)."""
        for action in plan:
            # preview=True lets Telegram expand the link preview card
            self.send_message(action["text"],
                              disable_preview=not action.get("preview", False),
                              buttons=action.get("buttons"))

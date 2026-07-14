"""Envoi de messages sur un canal Telegram (retries, backoff, 429)."""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)

TIMEOUT = 15
MAX_RETRIES = 4


class TelegramError(Exception):
    """Erreur réseau ou API Telegram."""


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
                    raise TelegramError(f"Échec réseau ({method}): {exc}") from exc
                time.sleep(min(2 ** attempt, 10))
                continue

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "1"))
                data = resp.json() if resp.content else {}
                wait = int(data.get("parameters", {}).get("retry_after", wait))
                log.warning("Telegram 429, attente %ss", wait)
                time.sleep(wait)
                continue
            data = resp.json() if resp.content else {}
            if resp.status_code == 401:
                raise TelegramError("Token de bot Telegram invalide (401).")
            if not resp.ok or not data.get("ok", False):
                desc = data.get("description", resp.text[:200])
                if resp.status_code >= 500 and attempt < MAX_RETRIES:
                    time.sleep(min(2 ** attempt, 10))
                    continue
                raise TelegramError(f"Telegram {method} a échoué: {desc}")
            return data["result"]
        raise TelegramError(f"Abandon après {MAX_RETRIES} tentatives ({method})")

    def get_me(self) -> dict:
        """Valide le token (--check)."""
        return self._post("getMe", {})

    @staticmethod
    def _markup(buttons: list[dict] | None) -> dict | None:
        """Construit un inline_keyboard depuis [{text,url}] : 2 boutons par ligne."""
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
        """Exécute un plan de messages texte (mode carte)."""
        for action in plan:
            # preview=True => on laisse Telegram déplier la carte du lien
            self.send_message(action["text"],
                              disable_preview=not action.get("preview", False),
                              buttons=action.get("buttons"))

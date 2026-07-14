"""Minimal OMDb client: fetch Rotten Tomatoes scores via IMDb IDs.

TMDB does not expose Rotten Tomatoes; OMDb (omdbapi.com) provides it in its
`Ratings` table. A free key is required (env OMDB_API_KEY or config omdb_api_key).
"""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)

# Note: the free OMDb key only works over HTTP (HTTPS is paid).
OMDB_URL = "http://www.omdbapi.com/"
TIMEOUT = 20
MAX_RETRIES = 3


class OMDbClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()

    def rt_score(self, imdb_id: str) -> int | None:
        """Tomatometer (0-100) for an IMDb ID, or None if unavailable."""
        if not imdb_id:
            return None
        resp = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.get(
                    OMDB_URL,
                    params={"apikey": self.api_key, "i": imdb_id},
                    timeout=TIMEOUT,
                )
                break
            except requests.RequestException as exc:
                if attempt == MAX_RETRIES:
                    log.warning("OMDb %s: aborted after %d attempts (%s)", imdb_id, MAX_RETRIES, exc)
                    return None
                time.sleep(min(2 ** attempt, 8))
        if resp is None or not resp.ok:
            return None
        data = resp.json()
        if data.get("Response") != "True":
            return None
        for rating in data.get("Ratings", []):
            if rating.get("Source") == "Rotten Tomatoes":
                val = rating.get("Value", "").rstrip("%")
                if val.isdigit():
                    return int(val)
        return None

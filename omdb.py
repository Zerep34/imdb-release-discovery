"""Client OMDb minimal : récupère la note Rotten Tomatoes via l'IMDb id.

TMDB n'expose pas Rotten Tomatoes ; OMDb (omdbapi.com) le fournit dans son
tableau `Ratings`. Clé gratuite requise (env OMDB_API_KEY ou config omdb_api_key).
"""
from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)

# NB : la clé OMDb gratuite ne fonctionne qu'en HTTP (HTTPS = tier payant).
OMDB_URL = "http://www.omdbapi.com/"
TIMEOUT = 20
MAX_RETRIES = 3


class OMDbClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()

    def rt_score(self, imdb_id: str) -> int | None:
        """Tomatometer (0-100) pour un imdb_id, ou None si indisponible."""
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
                    log.warning("OMDb %s: abandon après %d essais (%s)", imdb_id, MAX_RETRIES, exc)
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

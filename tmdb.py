"""Client TMDB v3 : requêtes de découverte + parsing en objets Release."""
from __future__ import annotations

import logging
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

import requests

log = logging.getLogger(__name__)

TMDB_BASE = "https://api.themoviedb.org/3"
ANIMATION_GENRE_ID = 16  # même id pour films et séries
TIMEOUT = 15
MAX_RETRIES = 4


@dataclass
class Release:
    """Un titre (film ou série) sorti dans la fenêtre visée."""

    media_type: str          # "movie" | "tv"
    tmdb_id: int
    title: str
    release_date: str        # ISO "2026-07-15" ou "" si inconnue
    year: str                # "2026" ou "" si inconnu
    popularity: float
    vote_average: float
    vote_count: int
    genre_ids: tuple[int, ...]
    arr_url: str = ""        # lien d'ajout Radarr/Sonarr (rempli après coup)
    cinema_url: str = ""     # lien recherche séances (sorties ciné, rempli après coup)
    imdb_id: str = ""        # "tt1234567" (rempli après coup, pour la carte IMDb)
    trailer_url: str = ""    # URL YouTube du trailer (rempli après coup)
    rt_score: int | None = None  # Tomatometer Rotten Tomatoes (via OMDb)
    sources: set[str] = field(default_factory=set)

    @property
    def is_animation(self) -> bool:
        return ANIMATION_GENRE_ID in self.genre_ids

    @property
    def is_cinema(self) -> bool:
        return any(s.startswith("Cinéma") for s in self.sources)

    @property
    def imdb_url(self) -> str:
        return f"https://www.imdb.com/title/{self.imdb_id}/" if self.imdb_id else ""

    @property
    def card_url(self) -> str:
        """URL dont Telegram déplie l'aperçu (carte) : IMDb sinon TMDB."""
        return self.imdb_url or self.tmdb_url

    @property
    def tmdb_url(self) -> str:
        return f"https://www.themoviedb.org/{self.media_type}/{self.tmdb_id}"

    @property
    def dedup_key(self) -> tuple[str, int]:
        return (self.media_type, self.tmdb_id)


def _parse_movie(raw: dict, source: str) -> Release:
    date = raw.get("release_date") or ""
    return Release(
        media_type="movie",
        tmdb_id=raw["id"],
        title=raw.get("title") or raw.get("original_title") or "?",
        release_date=date,
        year=date[:4],
        popularity=float(raw.get("popularity") or 0.0),
        vote_average=float(raw.get("vote_average") or 0.0),
        vote_count=int(raw.get("vote_count") or 0),
        genre_ids=tuple(raw.get("genre_ids") or ()),
        sources={source},
    )


def _parse_tv(raw: dict, source: str) -> Release:
    date = raw.get("first_air_date") or ""
    return Release(
        media_type="tv",
        tmdb_id=raw["id"],
        title=raw.get("name") or raw.get("original_name") or "?",
        release_date=date,
        year=date[:4],
        popularity=float(raw.get("popularity") or 0.0),
        vote_average=float(raw.get("vote_average") or 0.0),
        vote_count=int(raw.get("vote_count") or 0),
        genre_ids=tuple(raw.get("genre_ids") or ()),
        sources={source},
    )


class TMDBError(Exception):
    """Erreur réseau ou API TMDB (mène au code de sortie 2)."""


class TMDBClient:
    def __init__(self, api_key: str, language: str = "fr-FR", max_pages: int = 2):
        self.api_key = api_key
        self.language = language
        self.max_pages = max(1, max_pages)
        self.session = requests.Session()

    # --- requête bas niveau avec retries/backoff -------------------------
    def _get(self, path: str, params: dict) -> dict:
        params = {"api_key": self.api_key, "language": self.language, **params}
        url = f"{TMDB_BASE}{path}"
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, params=params, timeout=TIMEOUT)
            except requests.RequestException as exc:
                if attempt == MAX_RETRIES:
                    raise TMDBError(f"Échec réseau sur {path}: {exc}") from exc
                self._backoff(attempt)
                continue

            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", "1"))
                log.warning("TMDB 429, attente %ss", wait)
                time.sleep(wait)
                continue
            if resp.status_code == 401:
                raise TMDBError("Clé API TMDB invalide (401).")
            if resp.status_code >= 500 and attempt < MAX_RETRIES:
                self._backoff(attempt)
                continue
            if not resp.ok:
                raise TMDBError(f"TMDB {resp.status_code} sur {path}: {resp.text[:200]}")
            return resp.json()
        raise TMDBError(f"Abandon après {MAX_RETRIES} tentatives sur {path}")

    @staticmethod
    def _backoff(attempt: int) -> None:
        time.sleep(min(2 ** attempt, 10))

    # --- validation identifiants (--check) -------------------------------
    def get_configuration(self) -> dict:
        return self._get("/configuration", {})

    # --- bande-annonce (Trailer YouTube) --------------------------------
    def get_trailer_url(self, media_type: str, tmdb_id: int) -> str | None:
        """URL YouTube du meilleur trailer, ou None. Essaie langue puis en-US."""
        langs = [self.language] + ([] if self.language == "en-US" else ["en-US"])
        for lang in langs:
            try:
                data = self._get(f"/{media_type}/{tmdb_id}/videos", {"language": lang})
            except TMDBError as exc:
                log.warning("videos %s=%s: %s", media_type, tmdb_id, exc)
                continue
            key = _pick_trailer(data.get("results", []))
            if key:
                return f"https://www.youtube.com/watch?v={key}"
        return None

    # --- ids externes (tvdb pour Sonarr, imdb pour OMDb/Rotten Tomatoes) -
    def get_external_ids(self, media_type: str, tmdb_id: int) -> dict:
        """Retourne {tvdb_id, imdb_id, ...} ou {} en cas d'erreur."""
        try:
            return self._get(f"/{media_type}/{tmdb_id}/external_ids", {})
        except TMDBError as exc:
            log.warning("external_ids %s=%s: %s", media_type, tmdb_id, exc)
            return {}

    # --- résolution dynamique des plateformes ----------------------------
    def resolve_providers(self, media: str, region: str, names: Iterable[str]) -> dict[str, int]:
        """Mappe des noms de plateformes -> ids TMDB pour une région donnée.

        media : "movie" | "tv". Matching tolérant : casse, espaces, accents,
        ponctuation, et équivalence "+" <-> "plus". Fallback sous-chaîne.
        """
        data = self._get(f"/watch/providers/{media}", {"watch_region": region})
        catalog = [
            (_norm(prov.get("provider_name", "")), prov.get("provider_name", ""), prov["provider_id"])
            for prov in data.get("results", [])
        ]
        by_name = {norm: pid for norm, _raw, pid in catalog}

        resolved: dict[str, int] = {}
        for name in names:
            want = _norm(name)
            pid = by_name.get(want)
            if pid is None:
                # fallback : correspondance par sous-chaîne (ex. "Apple TV" ~ "Apple TV+")
                matches = [(raw, pid_) for norm, raw, pid_ in catalog
                           if want and (want in norm or norm in want)]
                if len(matches) == 1:
                    pid = matches[0][1]
                    log.info("Plateforme %r résolue en %r (%s/%s)",
                             name, matches[0][0], media, region)
                elif len(matches) > 1:
                    log.warning("Plateforme %r ambiguë (%s/%s) : %s — précisez le nom exact",
                                name, media, region, ", ".join(m[0] for m in matches))
            if pid is not None:
                resolved[name] = pid
            else:
                log.warning("Plateforme introuvable pour %s/%s: %r", media, region, name)
        return resolved

    # --- pagination générique -------------------------------------------
    def _discover(self, path: str, params: dict, source: str, parser) -> list[Release]:
        out: list[Release] = []
        for page in range(1, self.max_pages + 1):
            data = self._get(path, {**params, "page": page})
            results = data.get("results", [])
            for raw in results:
                out.append(parser(raw, source))
            if page >= int(data.get("total_pages", 1)):
                break
        return out

    # --- cinéma ----------------------------------------------------------
    def discover_cinema(self, region: str, week_start: str, week_end: str) -> list[Release]:
        params = {
            "region": region,
            "with_release_type": "3|2",
            "release_date.gte": week_start,
            "release_date.lte": week_end,
            "sort_by": "popularity.desc",
        }
        return self._discover("/discover/movie", params, f"Cinéma ({region})", _parse_movie)

    # --- streaming films -------------------------------------------------
    def discover_stream_movies(
        self, region: str, provider_ids: list[int], source: str,
        week_start: str, week_end: str,
    ) -> list[Release]:
        params = {
            "watch_region": region,
            "with_watch_providers": "|".join(str(i) for i in provider_ids),
            "with_watch_monetization_types": "flatrate",
            "primary_release_date.gte": week_start,
            "primary_release_date.lte": week_end,
            "sort_by": "popularity.desc",
        }
        return self._discover("/discover/movie", params, source, _parse_movie)

    # --- streaming séries ------------------------------------------------
    def discover_stream_tv(
        self, region: str, provider_ids: list[int], source: str,
        week_start: str, week_end: str, date_basis: str = "first_air",
    ) -> list[Release]:
        """date_basis : 'first_air' (premières de série S1) ou 'air'
        (tout show diffusant un épisode dans la fenêtre)."""
        field = "air_date" if date_basis == "air" else "first_air_date"
        params = {
            "watch_region": region,
            "with_watch_providers": "|".join(str(i) for i in provider_ids),
            "with_watch_monetization_types": "flatrate",
            f"{field}.gte": week_start,
            f"{field}.lte": week_end,
            "sort_by": "popularity.desc",
        }
        return self._discover("/discover/tv", params, source, _parse_tv)

    # --- première de saison (pour capter S2/S3) --------------------------
    def get_season_premiere(self, tv_id: int, week_start: str, week_end: str) -> dict | None:
        """Retourne {season_number, air_date} si une saison premiere dans la
        fenêtre, sinon None. Utilise /tv/{id} (liste des saisons)."""
        try:
            data = self._get(f"/tv/{tv_id}", {})
        except TMDBError as exc:
            log.warning("détails tv=%s: %s", tv_id, exc)
            return None
        return _pick_season_premiere(data.get("seasons", []), week_start, week_end)


def _pick_season_premiere(seasons: list[dict], week_start: str, week_end: str) -> dict | None:
    """Meilleure saison dont la date de première tombe dans la fenêtre.

    Ignore la saison 0 (spéciaux). En cas de plusieurs, garde le n° le plus élevé.
    """
    best = None
    for s in seasons:
        ad = s.get("air_date")
        num = s.get("season_number", 0)
        if not ad or num < 1 or not (week_start <= ad <= week_end):
            continue
        if best is None or num > best["season_number"]:
            best = {"season_number": num, "air_date": ad}
    return best


def _pick_trailer(results: list[dict]) -> str | None:
    """Choisit la meilleure clé vidéo YouTube : Trailer officiel > Trailer > Teaser."""
    yt = [v for v in results if v.get("site") == "YouTube" and v.get("key")]
    for pred in (
        lambda v: v.get("type") == "Trailer" and v.get("official"),
        lambda v: v.get("type") == "Trailer",
        lambda v: v.get("type") == "Teaser",
    ):
        for v in yt:
            if pred(v):
                return v["key"]
    return None


def _norm(s: str) -> str:
    """Normalise un nom de plateforme pour comparaison tolérante."""
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))  # retire accents
    s = s.replace("+", "plus").replace("&", "and")
    return "".join(c for c in s if c.isalnum())  # ne garde que alphanumérique

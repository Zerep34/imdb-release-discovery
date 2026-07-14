#!/usr/bin/env python3
"""CLI entry point: weekly releases (TMDB) -> Telegram channel.

Cross-platform (Windows / macOS / Linux). No hard-coded secrets:
configuration via JSON file, environment variables, or CLI arguments.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import formatter as fmt
import history
from omdb import OMDbClient
from telegram_client import TelegramClient, TelegramError
from tmdb import Release, TMDBClient, TMDBError

log = logging.getLogger("sorties")

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_NETWORK = 2

DEFAULTS = {
    "language": "fr-FR",
    "regions": ["FR"],
    "platforms": [],
    "include_cinema": True,
    "include_returning_seasons": True,
    "categories": ["films", "series", "animation", "animation_series"],
    "min_vote_count": 0,
    "min_popularity": 0,
    "max_items_per_section": 15,
    "max_pages": 2,
    "week_start_day": 2,
    "style": "card",
    "cinema_label": "Showtimes",
    "cinema_search_url": "https://www.allocine.fr/rechercher/?q={query}",
    "use_history": True,
    "history_file": "sent_history.json",
    "timezone": "Europe/Paris",
}


# --------------------------------------------------------------------------
# Date window: 7-day week anchored on `start_day`, in the configured time zone.
# start_day follows weekday(): Monday=0 ... Wednesday=2 ... Sunday=6.
# Default Wednesday (cinema release day in France) -> Wednesday through Tuesday.
# --------------------------------------------------------------------------
def week_window(when: str, tz_name: str, today: date | None = None,
                start_day: int = 2) -> tuple[date, date]:
    """Return (start, end) for the 7-day week anchored on `start_day`.

    when: "current" | "next" | "last". `today` is for tests; otherwise it is
    computed in the `tz_name` time zone.
    """
    if today is None:
        today = datetime.now(ZoneInfo(tz_name)).date()
    delta = (today.weekday() - start_day) % 7
    start = today - timedelta(days=delta)
    if when == "next":
        start += timedelta(days=7)
    elif when == "last":
        start -= timedelta(days=7)
    elif when != "current":
        raise ValueError(f"Unknown window: {when!r}")
    end = start + timedelta(days=6)
    return start, end


# --------------------------------------------------------------------------
# Deduplication: merge on (media_type, tmdb_id), aggregate sources.
# --------------------------------------------------------------------------
def dedup(releases: list[Release]) -> list[Release]:
    merged: dict[tuple[str, int], Release] = {}
    for rel in releases:
        key = rel.dedup_key
        if key in merged:
            existing = merged[key]
            existing.sources |= rel.sources
            # keep the highest observed popularity/rating
            existing.popularity = max(existing.popularity, rel.popularity)
            existing.vote_average = max(existing.vote_average, rel.vote_average)
            existing.vote_count = max(existing.vote_count, rel.vote_count)
        else:
            merged[key] = rel
    return list(merged.values())


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
def load_config(path: Path) -> dict:
    cfg = dict(DEFAULTS)
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    # secrets via environment variables (take precedence over the file)
    for env_key, cfg_key in (
        ("TMDB_API_KEY", "tmdb_api_key"),
        ("TELEGRAM_BOT_TOKEN", "telegram_bot_token"),
        ("TELEGRAM_CHAT_ID", "telegram_chat_id"),
        ("OMDB_API_KEY", "omdb_api_key"),
    ):
        if os.environ.get(env_key):
            cfg[cfg_key] = os.environ[env_key]
    return cfg


def apply_cli_overrides(cfg: dict, args: argparse.Namespace) -> dict:
    if args.regions:
        cfg["regions"] = [r.strip().upper() for r in args.regions.split(",") if r.strip()]
    if args.platforms:
        cfg["platforms"] = [p.strip() for p in args.platforms.split(",") if p.strip()]
    return cfg


def normalize_regions(regions: list[str]) -> list[str]:
    """Empty or ['ALL'] => worldwide (blank region on the TMDB side)."""
    if not regions or [r.upper() for r in regions] == ["ALL"]:
        return [""]  # "" = no region filter
    return [r.upper() for r in regions]


# --------------------------------------------------------------------------
# Collection
# --------------------------------------------------------------------------
def _in_window(rel: Release, ws: str, we: str) -> bool:
    """Reject any title whose release date falls outside the window.

    Client-side safety net: TMDB sometimes surfaces re-releases or catalog
    titles whose original date is old.
    """
    if not rel.release_date:
        return False
    return ws <= rel.release_date <= we


def _collect_returning_seasons(client: TMDBClient, region: str, provider_id: int,
                               source: str, ws: str, we: str) -> list[Release]:
    """S2+ season premieres: candidates from air_date, filtered to real season
    premieres inside the window. Title annotated with " - Season N"."""
    candidates = [
        rel for rel in client.discover_stream_tv(region, [provider_id], source, ws, we,
                                                  date_basis="air")
        # S1 is already covered by the first_air_date query
        if not (rel.release_date and ws <= rel.release_date <= we)
    ]
    if not candidates:
        return []

    # One /tv/{id} call per candidate: parallelized because they are independent.
    out: list[Release] = []
    with ThreadPoolExecutor(max_workers=min(8, len(candidates))) as pool:
        futures = {pool.submit(client.get_season_premiere, rel.tmdb_id, ws, we): rel
                   for rel in candidates}
        for fut in as_completed(futures):
            rel = futures[fut]
            prem = fut.result()
            if prem and prem["season_number"] >= 2:
                rel.release_date = prem["air_date"]
                rel.year = prem["air_date"][:4]  # season year, not S1 year
                rel.title = f"{rel.title} — Saison {prem['season_number']}"
                out.append(rel)
    return out


def collect(client: TMDBClient, cfg: dict, ws: str, we: str) -> list[Release]:
    regions = normalize_regions(cfg["regions"])
    platforms = cfg.get("platforms", [])
    all_rel: list[Release] = []

    for region in regions:
        region_label = region or "Monde"

        # Cinema
        if cfg.get("include_cinema", True):
            log.info("Cinema %s...", region_label)
            for rel in client.discover_cinema(region or "", ws, we):
                rel.sources = {f"Cinema ({region_label})"}
                all_rel.append(rel)

        # Streaming: needs a region for with_watch_providers.
        if platforms and region:
            movie_ids = client.resolve_providers("movie", region, platforms)
            tv_ids = client.resolve_providers("tv", region, platforms)
            for name in platforms:
                source = f"{name} ({region})"
                if name in movie_ids:
                    all_rel += client.discover_stream_movies(
                        region, [movie_ids[name]], source, ws, we)
                if name in tv_ids:
                    # new series (S1): filter first_air_date
                    all_rel += client.discover_stream_tv(
                        region, [tv_ids[name]], source, ws, we)
                    # new seasons (S2/S3): shows airing this week
                    # whose season premiere falls inside the window
                    if cfg.get("include_returning_seasons", True):
                        all_rel += _collect_returning_seasons(
                            client, region, tv_ids[name], source, ws, we)
        elif platforms and not region:
            log.warning("Streaming skipped: regions=ALL without a specific region "
                        "(TMDB requires watch_region).")

    in_window = [r for r in all_rel if _in_window(r, ws, we)]
    dropped = len(all_rel) - len(in_window)
    if dropped:
        log.info("%d title(s) dropped for being outside the window", dropped)
    return dedup(in_window)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def _enrich_one(client: TMDBClient, rel: Release, *, radarr: str, sonarr: str,
                omdb: OMDbClient | None, want_imdb: bool, trailers: bool,
                cinema_tpl: str) -> None:
    """Enrich a single Release (mutates the object in place). See `enrich`."""
    if trailers:
        rel.trailer_url = client.get_trailer_url(rel.media_type, rel.tmdb_id) or ""
    if cinema_tpl and rel.is_cinema:
        rel.cinema_url = cinema_tpl.replace("{query}", quote(rel.title))
    # external_ids required for imdb (card/RT) or tvdb (series + Sonarr).
    need_ext = bool(omdb) or want_imdb or (rel.media_type == "tv" and sonarr)
    ext = client.get_external_ids(rel.media_type, rel.tmdb_id) if need_ext else {}
    rel.imdb_id = ext.get("imdb_id") or ""

    if rel.media_type == "movie" and radarr:
        rel.arr_url = f"{radarr}/add/new?term=tmdb:{rel.tmdb_id}"
    elif rel.media_type == "tv" and sonarr:
        tvdb = ext.get("tvdb_id")
        term = f"tvdb:{tvdb}" if tvdb else rel.title
        rel.arr_url = f"{sonarr}/add/new?term={quote(term)}"

    if omdb:
        rel.rt_score = omdb.rt_score(rel.imdb_id)


def enrich(client: TMDBClient, buckets: dict, cfg: dict, want_imdb: bool = False) -> None:
    """Enrich each Release: Radarr/Sonarr link + imdb_id + Rotten Tomatoes score.

    - Radarr : term=tmdb:<id> (exact).
    - Sonarr: indexes by tvdbId -> resolved via external_ids (title fallback otherwise).
    - imdb_id: for the IMDb card (`card` mode) and Rotten Tomatoes.
    - Rotten Tomatoes: imdb_id -> OMDb. Skipped if no OMDb key is provided.

    A single external_ids call per title covers tvdb + imdb. Each title is
    independent: HTTP calls are parallelized (wall-clock bottleneck).
    """
    radarr = (cfg.get("radarr_url") or "").rstrip("/")
    sonarr = (cfg.get("sonarr_url") or "").rstrip("/")
    omdb_key = cfg.get("omdb_api_key") or ""
    omdb = OMDbClient(omdb_key) if omdb_key else None
    trailers = cfg.get("trailers", True)
    cinema_tpl = cfg.get("cinema_search_url") or ""
    if not radarr and not sonarr and not omdb and not want_imdb and not trailers and not cinema_tpl:
        return

    rels = [rel for bucket in buckets.values() for rel in bucket]
    if not rels:
        return

    workers = min(cfg.get("enrich_workers", 8), len(rels))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(_enrich_one, client, rel, radarr=radarr, sonarr=sonarr,
                        omdb=omdb, want_imdb=want_imdb, trailers=trailers,
                        cinema_tpl=cinema_tpl)
            for rel in rels
        ]
        for fut in as_completed(futures):
            fut.result()  # propagate any unexpected exception


def cmd_check(cfg: dict) -> int:
    ok = True
    try:
        TMDBClient(cfg["tmdb_api_key"], cfg["language"]).get_configuration()
        print("TMDB : OK")
    except (TMDBError, KeyError) as exc:
        print(f"TMDB: FAILURE - {exc}", file=sys.stderr)
        ok = False
    try:
        me = TelegramClient(cfg["telegram_bot_token"], cfg.get("telegram_chat_id", "")).get_me()
        print(f"Telegram : OK (@{me.get('username')})")
    except (TelegramError, KeyError) as exc:
        print(f"Telegram: FAILURE - {exc}", file=sys.stderr)
        ok = False
    return EXIT_OK if ok else EXIT_CONFIG


def resolve_style(cfg: dict, args: argparse.Namespace) -> str:
    """Determine the delivery style: 'card' | 'text'.

    Priority: --text > cfg['style']. Default: 'card'.
    """
    if args.text:
        return "text"
    style = (cfg.get("style") or "").lower()
    return style if style in ("card", "text") else "card"


def _dry_run_plan(plan: list[dict]) -> None:
    print(f"\n=== DRY-RUN (card) : {len(plan)} envoi(s) ===\n")
    for a in plan:
        tag = " [preview]" if a.get("preview") else ""
        print(f"{a['text']}{tag}")
        for b in a.get("buttons", []):
            print(f"   [🔘 {b['text']} -> {b['url']}]")
        print()


def _dry_run_messages(messages: list[str]) -> None:
    print(f"\n=== DRY-RUN : {len(messages)} message(s) ===\n")
    for i, msg in enumerate(messages, 1):
        print(f"--- message {i}/{len(messages)} ---")
        print(msg)
        print()


def run(cfg: dict, args: argparse.Namespace) -> int:
    ws_date, we_date = week_window(args.week, cfg["timezone"],
                                   start_day=cfg.get("week_start_day", 2))
    ws, we = ws_date.isoformat(), we_date.isoformat()
    log.info("Window: %s -> %s", ws, we)

    client = TMDBClient(cfg["tmdb_api_key"], cfg["language"], cfg.get("max_pages", 2))
    releases = collect(client, cfg, ws, we)
    log.info("%d title(s) after deduplication", len(releases))

    buckets = fmt.classify(
        releases,
        cfg["categories"],
        cfg["max_items_per_section"],
        cfg.get("min_vote_count", 0),
        cfg.get("min_popularity", 0),
    )
    # Anti-duplicate memory: remove already-posted titles.
    hist_path = Path(cfg.get("history_file", "sent_history.json"))
    use_history = cfg.get("use_history", True) and not args.ignore_history
    seen: set[str] = set()
    kept: list[str] = []
    if use_history:
        total = sum(len(v) for v in buckets.values())
        seen = history.load(hist_path)
        kept = history.prune(buckets, seen)
        log.info("History: %d new, %d already-seen ignored",
                 len(kept), total - len(kept))

    style = resolve_style(cfg, args)
    log.info("Message style: %s", style)
    enrich(client, buckets, cfg, want_imdb=(style == "card"))

    # Build the send plan according to the style, then print (dry-run) or send.
    if style == "card":
        cinema_label = cfg.get("cinema_label", "Showtimes")
        plan = fmt.build_card_plan(buckets, ws_date, we_date, cfg["categories"], cinema_label)
        if args.dry_run:
            _dry_run_plan(plan)
            return EXIT_OK
        tg = TelegramClient(cfg["telegram_bot_token"], cfg["telegram_chat_id"])
        tg.send_plan(plan)
        log.info("%d send action(s) to %s", len(plan), cfg["telegram_chat_id"])
    else:
        messages = fmt.build_messages(buckets, ws_date, we_date, cfg["categories"])
        if args.dry_run:
            _dry_run_messages(messages)
            return EXIT_OK
        tg = TelegramClient(cfg["telegram_bot_token"], cfg["telegram_chat_id"])
        tg.send_all(messages)
        log.info("%d message(s) sent to %s", len(messages), cfg["telegram_chat_id"])

    if use_history:
        history.save(hist_path, seen | set(kept))
    return EXIT_OK


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Weekly releases -> Telegram")
    p.add_argument("--config", default="config.json", help="Config file path")
    p.add_argument("--regions", help="Override regions, e.g. FR,US")
    p.add_argument("--platforms", help='Override platforms, e.g. "Netflix,Max"')
    p.add_argument("--week", choices=["current", "next", "last"], default="current")
    p.add_argument("--text", action="store_true",
                   help="Force grouped text mode (instead of card mode)")
    p.add_argument("--ignore-history", action="store_true",
                   help="Do not filter already-posted titles (does not write history in dry-run)")
    p.add_argument("--dry-run", action="store_true", help="Print without sending")
    p.add_argument("--check", action="store_true", help="Validate credentials and exit")
    p.add_argument("--verbose", action="store_true", help="Verbose logs")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    cfg = load_config(Path(args.config))
    cfg = apply_cli_overrides(cfg, args)

    if not cfg.get("tmdb_api_key") or not cfg.get("telegram_bot_token"):
        log.error("Incomplete config: tmdb_api_key and telegram_bot_token are required.")
        return EXIT_CONFIG

    try:
        if args.check:
            return cmd_check(cfg)
        return run(cfg, args)
    except (TMDBError, TelegramError) as exc:
        log.error("Network/API error: %s", exc)
        return EXIT_NETWORK
    except KeyError as exc:
        log.error("Missing config key: %s", exc)
        return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())

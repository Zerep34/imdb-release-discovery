#!/usr/bin/env python3
"""Point d'entrée CLI : sorties de la semaine (TMDB) -> canal Telegram.

Cross-platform (Windows / macOS / Linux). Aucun secret codé en dur :
config par fichier JSON, variables d'environnement ou arguments CLI.
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
    "cinema_label": "Séances",
    "cinema_search_url": "https://www.allocine.fr/rechercher/?q={query}",
    "use_history": True,
    "history_file": "sent_history.json",
    "timezone": "Europe/Paris",
}


# --------------------------------------------------------------------------
# Fenêtre de dates : semaine de 7 jours ancrée sur `start_day`, dans le fuseau.
# start_day suit weekday() : lundi=0 ... mercredi=2 ... dimanche=6.
# Par défaut mercredi (jour des sorties ciné en France) -> mercredi->mardi.
# --------------------------------------------------------------------------
def week_window(when: str, tz_name: str, today: date | None = None,
                start_day: int = 2) -> tuple[date, date]:
    """Retourne (début, fin) de la semaine de 7 jours ancrée sur `start_day`.

    when : "current" | "next" | "last". `today` sert aux tests ; sinon
    calculé dans le fuseau `tz_name`.
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
        raise ValueError(f"Fenêtre inconnue: {when!r}")
    end = start + timedelta(days=6)
    return start, end


# --------------------------------------------------------------------------
# Dédoublonnage : fusion sur (media_type, tmdb_id), agrégation des sources.
# --------------------------------------------------------------------------
def dedup(releases: list[Release]) -> list[Release]:
    merged: dict[tuple[str, int], Release] = {}
    for rel in releases:
        key = rel.dedup_key
        if key in merged:
            existing = merged[key]
            existing.sources |= rel.sources
            # garder la popularité/note la plus élevée observée
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
    # secrets via env (prioritaires sur le fichier)
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
    """Vide ou ['ALL'] => monde (region vide côté TMDB)."""
    if not regions or [r.upper() for r in regions] == ["ALL"]:
        return [""]  # "" = pas de filtre région
    return [r.upper() for r in regions]


# --------------------------------------------------------------------------
# Collecte
# --------------------------------------------------------------------------
def _in_window(rel: Release, ws: str, we: str) -> bool:
    """Rejette tout titre dont la date de sortie sort de la fenêtre.

    Filet de sécurité côté client : TMDB laisse parfois passer des ressorties
    ou des titres au catalogue dont la date d'origine est ancienne.
    """
    if not rel.release_date:
        return False
    return ws <= rel.release_date <= we


def _collect_returning_seasons(client: TMDBClient, region: str, provider_id: int,
                               source: str, ws: str, we: str) -> list[Release]:
    """Premières de saison S2+ : candidats via air_date, filtrés sur une vraie
    première de saison dans la fenêtre. Titre annoté « — Saison N »."""
    candidates = [
        rel for rel in client.discover_stream_tv(region, [provider_id], source, ws, we,
                                                  date_basis="air")
        # S1 déjà couvert par la requête first_air_date
        if not (rel.release_date and ws <= rel.release_date <= we)
    ]
    if not candidates:
        return []

    # Un appel /tv/{id} par candidat : parallélisé (indépendants).
    out: list[Release] = []
    with ThreadPoolExecutor(max_workers=min(8, len(candidates))) as pool:
        futures = {pool.submit(client.get_season_premiere, rel.tmdb_id, ws, we): rel
                   for rel in candidates}
        for fut in as_completed(futures):
            rel = futures[fut]
            prem = fut.result()
            if prem and prem["season_number"] >= 2:
                rel.release_date = prem["air_date"]
                rel.year = prem["air_date"][:4]  # année de la saison, pas de la S1
                rel.title = f"{rel.title} — Saison {prem['season_number']}"
                out.append(rel)
    return out


def collect(client: TMDBClient, cfg: dict, ws: str, we: str) -> list[Release]:
    regions = normalize_regions(cfg["regions"])
    platforms = cfg.get("platforms", [])
    all_rel: list[Release] = []

    for region in regions:
        region_label = region or "Monde"

        # Cinéma
        if cfg.get("include_cinema", True):
            log.info("Cinéma %s...", region_label)
            for rel in client.discover_cinema(region or "", ws, we):
                rel.sources = {f"Cinéma ({region_label})"}
                all_rel.append(rel)

        # Streaming : besoin d'une région pour with_watch_providers.
        if platforms and region:
            movie_ids = client.resolve_providers("movie", region, platforms)
            tv_ids = client.resolve_providers("tv", region, platforms)
            for name in platforms:
                source = f"{name} ({region})"
                if name in movie_ids:
                    all_rel += client.discover_stream_movies(
                        region, [movie_ids[name]], source, ws, we)
                if name in tv_ids:
                    # nouvelles séries (S1) : filtre first_air_date
                    all_rel += client.discover_stream_tv(
                        region, [tv_ids[name]], source, ws, we)
                    # nouvelles saisons (S2/S3) : shows diffusant cette semaine
                    # dont une première de saison tombe dans la fenêtre
                    if cfg.get("include_returning_seasons", True):
                        all_rel += _collect_returning_seasons(
                            client, region, tv_ids[name], source, ws, we)
        elif platforms and not region:
            log.warning("Streaming ignoré : régions=ALL sans région précise "
                        "(TMDB exige watch_region).")

    in_window = [r for r in all_rel if _in_window(r, ws, we)]
    dropped = len(all_rel) - len(in_window)
    if dropped:
        log.info("%d titre(s) hors fenêtre écarté(s)", dropped)
    return dedup(in_window)


# --------------------------------------------------------------------------
# Commandes
# --------------------------------------------------------------------------
def _enrich_one(client: TMDBClient, rel: Release, *, radarr: str, sonarr: str,
                omdb: OMDbClient | None, want_imdb: bool, trailers: bool,
                cinema_tpl: str) -> None:
    """Enrichit un seul Release (mute l'objet en place). Voir `enrich`."""
    if trailers:
        rel.trailer_url = client.get_trailer_url(rel.media_type, rel.tmdb_id) or ""
    if cinema_tpl and rel.is_cinema:
        rel.cinema_url = cinema_tpl.replace("{query}", quote(rel.title))
    # external_ids requis pour : imdb (carte/RT) ou tvdb (série+Sonarr).
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
    """Enrichit chaque Release : lien Radarr/Sonarr + imdb_id + note Rotten Tomatoes.

    - Radarr : term=tmdb:<id> (exact).
    - Sonarr : indexe par tvdbId -> résolu via external_ids (repli titre sinon).
    - imdb_id : pour la carte IMDb (mode `card`) et Rotten Tomatoes.
    - Rotten Tomatoes : imdb_id -> OMDb. Ignoré si pas de clé OMDb.

    Un seul appel external_ids par titre couvre tvdb + imdb. Chaque titre est
    indépendant : les appels HTTP sont parallélisés (goulot du temps mur).
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
            fut.result()  # propage toute exception inattendue


def cmd_check(cfg: dict) -> int:
    ok = True
    try:
        TMDBClient(cfg["tmdb_api_key"], cfg["language"]).get_configuration()
        print("TMDB : OK")
    except (TMDBError, KeyError) as exc:
        print(f"TMDB : ÉCHEC — {exc}", file=sys.stderr)
        ok = False
    try:
        me = TelegramClient(cfg["telegram_bot_token"], cfg.get("telegram_chat_id", "")).get_me()
        print(f"Telegram : OK (@{me.get('username')})")
    except (TelegramError, KeyError) as exc:
        print(f"Telegram : ÉCHEC — {exc}", file=sys.stderr)
        ok = False
    return EXIT_OK if ok else EXIT_CONFIG


def resolve_style(cfg: dict, args: argparse.Namespace) -> str:
    """Détermine le style d'envoi : 'card' | 'text'.

    Priorité : --text > cfg['style']. Défaut : 'card'.
    """
    if args.text:
        return "text"
    style = (cfg.get("style") or "").lower()
    return style if style in ("card", "text") else "card"


def _dry_run_plan(plan: list[dict]) -> None:
    print(f"\n=== DRY-RUN (card) : {len(plan)} envoi(s) ===\n")
    for a in plan:
        tag = " [aperçu]" if a.get("preview") else ""
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
    log.info("Fenêtre : %s -> %s", ws, we)

    client = TMDBClient(cfg["tmdb_api_key"], cfg["language"], cfg.get("max_pages", 2))
    releases = collect(client, cfg, ws, we)
    log.info("%d titre(s) après dédoublonnage", len(releases))

    buckets = fmt.classify(
        releases,
        cfg["categories"],
        cfg["max_items_per_section"],
        cfg.get("min_vote_count", 0),
        cfg.get("min_popularity", 0),
    )
    # Mémoire anti-répétition : retire les titres déjà postés.
    hist_path = Path(cfg.get("history_file", "sent_history.json"))
    use_history = cfg.get("use_history", True) and not args.ignore_history
    seen: set[str] = set()
    kept: list[str] = []
    if use_history:
        total = sum(len(v) for v in buckets.values())
        seen = history.load(hist_path)
        kept = history.prune(buckets, seen)
        log.info("Historique : %d nouveau(x), %d déjà vu(s) ignoré(s)",
                 len(kept), total - len(kept))

    style = resolve_style(cfg, args)
    log.info("Style de message : %s", style)
    enrich(client, buckets, cfg, want_imdb=(style == "card"))

    # Construit le plan d'envoi selon le style, puis affiche (dry-run) ou envoie.
    if style == "card":
        cinema_label = cfg.get("cinema_label", "Séances")
        plan = fmt.build_card_plan(buckets, ws_date, we_date, cfg["categories"], cinema_label)
        if args.dry_run:
            _dry_run_plan(plan)
            return EXIT_OK
        tg = TelegramClient(cfg["telegram_bot_token"], cfg["telegram_chat_id"])
        tg.send_plan(plan)
        log.info("%d envoi(s) sur %s", len(plan), cfg["telegram_chat_id"])
    else:
        messages = fmt.build_messages(buckets, ws_date, we_date, cfg["categories"])
        if args.dry_run:
            _dry_run_messages(messages)
            return EXIT_OK
        tg = TelegramClient(cfg["telegram_bot_token"], cfg["telegram_chat_id"])
        tg.send_all(messages)
        log.info("%d message(s) envoyé(s) sur %s", len(messages), cfg["telegram_chat_id"])

    if use_history:
        history.save(hist_path, seen | set(kept))
    return EXIT_OK


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Sorties de la semaine -> Telegram")
    p.add_argument("--config", default="config.json", help="Chemin du fichier de config")
    p.add_argument("--regions", help="Surcharge des régions, ex: FR,US")
    p.add_argument("--platforms", help='Surcharge des plateformes, ex: "Netflix,Max"')
    p.add_argument("--week", choices=["current", "next", "last"], default="current")
    p.add_argument("--text", action="store_true",
                   help="Force le mode texte groupé (au lieu du mode carte)")
    p.add_argument("--ignore-history", action="store_true",
                   help="Ne pas filtrer les titres déjà postés (n'écrit pas l'historique en dry-run)")
    p.add_argument("--dry-run", action="store_true", help="Affiche sans envoyer")
    p.add_argument("--check", action="store_true", help="Valide les identifiants puis quitte")
    p.add_argument("--verbose", action="store_true", help="Logs détaillés")
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
        log.error("Config incomplète : tmdb_api_key et telegram_bot_token requis.")
        return EXIT_CONFIG

    try:
        if args.check:
            return cmd_check(cfg)
        return run(cfg, args)
    except (TMDBError, TelegramError) as exc:
        log.error("Erreur réseau/API : %s", exc)
        return EXIT_NETWORK
    except KeyError as exc:
        log.error("Clé de config manquante : %s", exc)
        return EXIT_CONFIG


if __name__ == "__main__":
    sys.exit(main())

"""Tests du dédoublonnage : fusion (media_type, tmdb_id) + agrégation sources."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from releases_to_telegram import _in_window, dedup  # noqa: E402
from tmdb import Release, _pick_season_premiere  # noqa: E402


def test_season_premiere_picks_in_window():
    seasons = [
        {"season_number": 0, "air_date": "2026-07-09"},  # spéciaux, ignoré
        {"season_number": 1, "air_date": "2024-01-01"},  # hors fenêtre
        {"season_number": 2, "air_date": "2026-07-09"},  # dans fenêtre
    ]
    p = _pick_season_premiere(seasons, "2026-07-08", "2026-07-14")
    assert p == {"season_number": 2, "air_date": "2026-07-09"}


def test_season_premiere_highest_number_wins():
    seasons = [
        {"season_number": 2, "air_date": "2026-07-08"},
        {"season_number": 3, "air_date": "2026-07-10"},
    ]
    assert _pick_season_premiere(seasons, "2026-07-08", "2026-07-14")["season_number"] == 3


def test_season_premiere_none_when_no_match():
    seasons = [{"season_number": 1, "air_date": "2020-01-01"}, {"season_number": 2, "air_date": None}]
    assert _pick_season_premiere(seasons, "2026-07-08", "2026-07-14") is None


def mk(mt, tid, source, pop=1.0, va=0.0, vc=0):
    return Release(
        media_type=mt, tmdb_id=tid, title=f"T{tid}",
        release_date="2026-07-15", year="2026",
        popularity=pop, vote_average=va, vote_count=vc, genre_ids=(),
        sources={source},
    )


def test_merges_same_id_across_platforms():
    out = dedup([
        mk("movie", 10, "Netflix (FR)", pop=5.0, va=6.0, vc=100),
        mk("movie", 10, "Cinéma (FR)", pop=8.0, va=7.0, vc=250),
    ])
    assert len(out) == 1
    rel = out[0]
    assert rel.sources == {"Netflix (FR)", "Cinéma (FR)"}
    # agrégats : max conservé
    assert rel.popularity == 8.0
    assert rel.vote_average == 7.0
    assert rel.vote_count == 250


def test_different_media_types_not_merged():
    out = dedup([mk("movie", 10, "Netflix (FR)"), mk("tv", 10, "Netflix (FR)")])
    assert len(out) == 2


def test_different_ids_not_merged():
    out = dedup([mk("movie", 1, "a"), mk("movie", 2, "b")])
    assert len(out) == 2


def test_empty():
    assert dedup([]) == []


def _with_date(d):
    r = mk("movie", 1, "a")
    r.release_date = d
    return r


def test_in_window_filters_old_and_future():
    ws, we = "2026-07-13", "2026-07-19"
    assert _in_window(_with_date("2026-07-15"), ws, we) is True   # dedans
    assert _in_window(_with_date("2026-07-13"), ws, we) is True   # borne basse
    assert _in_window(_with_date("2026-07-19"), ws, we) is True   # borne haute
    assert _in_window(_with_date("1990-01-01"), ws, we) is False  # ressortie
    assert _in_window(_with_date("2026-07-20"), ws, we) is False  # semaine suivante
    assert _in_window(_with_date(""), ws, we) is False            # date inconnue

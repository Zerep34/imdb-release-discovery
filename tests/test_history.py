"""Tests de la mémoire anti-répétition (history.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import history  # noqa: E402
from tmdb import Release  # noqa: E402


def mk(mt, tid, date="2026-07-15"):
    return Release(media_type=mt, tmdb_id=tid, title=f"T{tid}",
                   release_date=date, year=date[:4], popularity=1.0,
                   vote_average=0.0, vote_count=0, genre_ids=())


def test_rel_key_includes_date_distinguishes_seasons():
    s1 = mk("tv", 10, "2024-01-01")
    s2 = mk("tv", 10, "2026-07-09")   # même show, saison différente
    assert history.rel_key(s1) != history.rel_key(s2)


def test_load_missing_returns_empty(tmp_path):
    assert history.load(tmp_path / "nope.json") == set()


def test_save_load_roundtrip(tmp_path):
    p = tmp_path / "h.json"
    keys = {"movie:1:2026-07-15", "tv:2:2026-07-09"}
    history.save(p, keys)
    assert history.load(p) == keys


def test_load_corrupt_returns_empty(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert history.load(p) == set()


def test_prune_drops_seen_and_returns_kept():
    buckets = {"films": [mk("movie", 1), mk("movie", 2)], "series": [mk("tv", 3)]}
    seen = {history.rel_key(mk("movie", 1))}
    kept = history.prune(buckets, seen)
    assert [r.tmdb_id for r in buckets["films"]] == [2]
    assert [r.tmdb_id for r in buckets["series"]] == [3]
    assert set(kept) == {history.rel_key(mk("movie", 2)), history.rel_key(mk("tv", 3))}


def test_prune_empty_when_all_seen():
    buckets = {"films": [mk("movie", 1)]}
    kept = history.prune(buckets, {history.rel_key(mk("movie", 1))})
    assert buckets["films"] == [] and kept == []

"""Enrichment tests: *arr links, IMDb, trailer, cinema, RT - parallelized."""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import releases_to_telegram as rt  # noqa: E402
from tmdb import Release  # noqa: E402


def mk(mt, tid, source="Netflix (FR)", title=None):
    return Release(
        media_type=mt, tmdb_id=tid, title=title or f"T{tid}",
        release_date="2026-07-15", year="2026",
        popularity=1.0, vote_average=0.0, vote_count=0, genre_ids=(),
        sources={source},
    )


class FakeClient:
    """Minimal TMDB client: deterministic values plus call log."""

    def __init__(self, ext=None, trailer="vid"):
        self.ext = ext or {}
        self.trailer = trailer
        self.calls = []
        self._lock = threading.Lock()

    def get_trailer_url(self, media_type, tmdb_id):
        with self._lock:
            self.calls.append(("trailer", tmdb_id))
        return f"https://youtu.be/{self.trailer}" if self.trailer else None

    def get_external_ids(self, media_type, tmdb_id):
        with self._lock:
            self.calls.append(("ext", tmdb_id))
        return dict(self.ext)


# Config that enables ONLY the field under test (trailers off by default).
def _cfg(**kw):
    base = {"trailers": False, "cinema_search_url": ""}
    base.update(kw)
    return base


def test_enrich_noop_when_nothing_configured():
    client = FakeClient()
    rt.enrich(client, {"films": [mk("movie", 1)]}, _cfg(), want_imdb=False)
    assert client.calls == []


def test_enrich_sets_radarr_trailer_and_cinema():
    client = FakeClient(trailer="abc")
    m = mk("movie", 5, source="Cinema (FR)")  # is_cinema -> cinema_url
    cfg = _cfg(radarr_url="http://r:7878/", trailers=True,
               cinema_search_url="https://cine/?q={query}")
    rt.enrich(client, {"films": [m]}, cfg, want_imdb=False)
    assert m.arr_url == "http://r:7878/add/new?term=tmdb:5"
    assert m.trailer_url == "https://youtu.be/abc"
    assert m.cinema_url == "https://cine/?q=T5"
    # movie + Radarr without omdb/want_imdb: no external_ids needed
    assert ("ext", 5) not in client.calls
    assert m.imdb_id == ""


def test_enrich_tv_sonarr_uses_tvdb():
    client = FakeClient(ext={"tvdb_id": 99, "imdb_id": "tt1"})
    t = mk("tv", 7)
    cfg = _cfg(sonarr_url="http://s:8989")
    rt.enrich(client, {"series": [t]}, cfg, want_imdb=False)
    assert t.arr_url == "http://s:8989/add/new?term=tvdb%3A99"  # quote() encodes ':'
    assert t.imdb_id == "tt1"
    assert ("ext", 7) in client.calls


def test_enrich_tv_sonarr_falls_back_to_title_without_tvdb():
    client = FakeClient(ext={})  # no tvdb_id
    t = mk("tv", 7, title="Mon Show")
    rt.enrich(client, {"series": [t]}, _cfg(sonarr_url="http://s:8989"), want_imdb=False)
    assert t.arr_url == "http://s:8989/add/new?term=Mon%20Show"


def test_enrich_rt_score_via_omdb(monkeypatch):
    class FakeOMDb:
        def __init__(self, key):
            pass

        def rt_score(self, imdb_id):
            return 91 if imdb_id == "tt1" else None

    monkeypatch.setattr(rt, "OMDbClient", FakeOMDb)
    client = FakeClient(ext={"imdb_id": "tt1"})
    m = mk("movie", 1)
    rt.enrich(client, {"films": [m]}, _cfg(omdb_api_key="k"), want_imdb=False)
    assert m.imdb_id == "tt1"
    assert m.rt_score == 91


def test_enrich_want_imdb_fetches_external_ids():
    client = FakeClient(ext={"imdb_id": "tt9"})
    m = mk("movie", 1)
    rt.enrich(client, {"films": [m]}, _cfg(), want_imdb=True)
    assert m.imdb_id == "tt9"
    assert ("ext", 1) in client.calls


def test_enrich_processes_every_item():
    client = FakeClient(trailer="x")
    rels = [mk("movie", i) for i in range(1, 21)]
    buckets = {"films": rels[:10], "series": rels[10:]}
    rt.enrich(client, buckets, _cfg(trailers=True), want_imdb=False)
    assert all(r.trailer_url == "https://youtu.be/x" for r in rels)
    trailer_ids = sorted(tid for kind, tid in client.calls if kind == "trailer")
    assert trailer_ids == list(range(1, 21))


def test_enrich_runs_in_parallel():
    """Parallelism proof: N threads must reach the barrier simultaneously,
    otherwise barrier.wait() raises BrokenBarrierError (timeout)."""
    n = 4
    barrier = threading.Barrier(n, timeout=5)

    class BarrierClient:
        def __init__(self):
            self.max_active = 0
            self._active = 0
            self._lock = threading.Lock()

        def get_trailer_url(self, media_type, tmdb_id):
            with self._lock:
                self._active += 1
                self.max_active = max(self.max_active, self._active)
            barrier.wait()  # block until n threads are simultaneous
            with self._lock:
                self._active -= 1
            return None

        def get_external_ids(self, media_type, tmdb_id):
            return {}

    client = BarrierClient()
    rels = [mk("movie", i) for i in range(n)]
    cfg = _cfg(trailers=True, enrich_workers=n)
    rt.enrich(client, {"films": rels}, cfg, want_imdb=False)
    assert client.max_active == n  # the n titles were processed in parallel

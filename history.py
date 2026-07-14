"""Memory of already-posted titles, used to avoid weekly duplicates
(a cinema release running for several weeks is only posted once).

State: a small local JSON file (list of keys). A title key is:
`media_type:tmdb_id:release_date` - the date distinguishes seasons (S2/S3 of
the same show share a tmdb_id but have different premiere dates).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from tmdb import Release

log = logging.getLogger(__name__)


def rel_key(rel: Release) -> str:
    return f"{rel.media_type}:{rel.tmdb_id}:{rel.release_date}"


def load(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data.get("sent", []))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Unreadable history (%s), starting from scratch", exc)
        return set()


def save(path: Path, keys: set[str]) -> None:
    path.write_text(json.dumps({"sent": sorted(keys)}, ensure_ascii=False, indent=0),
                    encoding="utf-8")


def prune(buckets: dict[str, list[Release]], seen: set[str]) -> list[str]:
    """Remove titles already posted (present in `seen`) from the buckets.

    Returns the keys for the titles kept (new titles to remember after sending).
    """
    kept: list[str] = []
    for cat, rels in buckets.items():
        fresh = []
        for rel in rels:
            k = rel_key(rel)
            if k in seen:
                continue
            fresh.append(rel)
            kept.append(k)
        buckets[cat] = fresh
    return kept

"""Mémoire des titres déjà postés, pour éviter les répétitions d'une semaine
sur l'autre (un film en salle plusieurs semaines n'est posté qu'une fois).

État : petit fichier JSON local (liste de clés). Clé d'un titre :
`media_type:tmdb_id:release_date` — la date distingue les saisons (S2/S3 d'un
même show ont un tmdb_id identique mais une date de première différente).
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
        log.warning("Historique illisible (%s), on repart de zéro", exc)
        return set()


def save(path: Path, keys: set[str]) -> None:
    path.write_text(json.dumps({"sent": sorted(keys)}, ensure_ascii=False, indent=0),
                    encoding="utf-8")


def prune(buckets: dict[str, list[Release]], seen: set[str]) -> list[str]:
    """Retire des buckets les titres déjà postés (présents dans `seen`).

    Retourne les clés des titres conservés (= nouveaux, à mémoriser après envoi).
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

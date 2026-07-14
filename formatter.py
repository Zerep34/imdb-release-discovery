"""Classement par catégorie + mise en forme HTML découpée pour Telegram."""
from __future__ import annotations

from datetime import date

from tmdb import Release

TELEGRAM_LIMIT = 4096

# Ordre d'affichage des sections + en-têtes emoji.
SECTION_HEADERS = {
    "films": "🎬 Films",
    "series": "📺 Séries",
    "animation": "🧸 Animation",
    "animation_series": "🧸📺 Séries d'animation",
}
SECTION_ORDER = ["films", "series", "animation", "animation_series"]

# Emoji par catégorie, réutilisé pour les légendes des affiches.
CATEGORY_EMOJI = {
    "films": "🎬",
    "series": "📺",
    "animation": "🧸",
    "animation_series": "🧸📺",
}

# Libellé singulier (mode carte, un message par titre).
CATEGORY_LABEL = {
    "films": "Film",
    "series": "Série",
    "animation": "Animation",
    "animation_series": "Série d'animation",
}


def label_for(rel: Release, category: str) -> tuple[str, str]:
    """(emoji, libellé) d'un titre. Met en avant les sorties cinéma (🍿)."""
    if rel.is_cinema and category in ("films", "animation"):
        emoji = "🍿"
        label = "Animation au cinéma" if category == "animation" else "Au cinéma"
        return emoji, label
    return CATEGORY_EMOJI.get(category, "🎬"), CATEGORY_LABEL.get(category, "Film")


_MONTHS_FR = [
    "", "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def category_of(rel: Release) -> str:
    """Catégorie d'un Release selon media_type + genre Animation (16)."""
    if rel.media_type == "movie":
        return "animation" if rel.is_animation else "films"
    return "animation_series" if rel.is_animation else "series"


def classify(
    releases: list[Release],
    categories: list[str],
    max_items: int,
    min_vote_count: int = 0,
    min_popularity: float = 0.0,
) -> dict[str, list[Release]]:
    """Range les releases par catégorie, filtre, trie par popularité, tronque.

    `min_popularity` écarte les petites productions / films confidentiels
    (la popularité TMDB reflète le buzz actuel, pertinent pour des sorties
    fraîches sans encore de votes).
    Ne produit que les catégories demandées.
    """
    buckets: dict[str, list[Release]] = {c: [] for c in categories}
    for rel in releases:
        if rel.vote_count < min_vote_count:
            continue
        if rel.popularity < min_popularity:
            continue
        cat = category_of(rel)
        if cat in buckets:
            buckets[cat].append(rel)
    for cat in buckets:
        # sorties cinéma en tête, puis par popularité décroissante
        buckets[cat].sort(key=lambda r: (not r.is_cinema, -r.popularity))
        del buckets[cat][max_items:]
    return buckets


def _fr_date(d: date) -> str:
    return f"{d.day} {_MONTHS_FR[d.month]} {d.year}"


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _format_line(rel: Release) -> str:
    title = _escape(rel.title)
    prefix = "🍿 " if rel.is_cinema else "• "
    line = f'{prefix}<a href="{rel.tmdb_url}">{title}</a>'
    if rel.year:
        line += f" ({rel.year})"
    sources = ", ".join(sorted(rel.sources))
    if sources:
        line += f" — {sources}"
    if rel.vote_average > 0:
        line += f" · ⭐ {rel.vote_average:.1f}"
    if rel.rt_score is not None:
        line += f" · 🍅 {rel.rt_score}%"
    if rel.arr_url:
        label = "Sonarr" if rel.media_type == "tv" else "Radarr"
        line += f' · <a href="{rel.arr_url}">➕ {label}</a>'
    return line


def build_messages(
    buckets: dict[str, list[Release]],
    week_start: date,
    week_end: date,
    categories: list[str],
) -> list[str]:
    """Construit la liste des messages (<4096 chars) prêts pour sendMessage.

    Découpe entre lignes / sections, jamais au milieu d'une balise HTML.
    """
    header = header_text(week_start, week_end)

    total = sum(len(buckets.get(c, [])) for c in categories)
    if total == 0:
        return [f"{header}\n\nAucune sortie détectée cette semaine."]

    # Liste de blocs de lignes ; chaque ligne est atomique (jamais coupée).
    lines: list[str] = [header]
    for cat in SECTION_ORDER:
        if cat not in categories:
            continue
        items = buckets.get(cat, [])
        if not items:
            continue
        lines.append("")  # séparateur
        lines.append(f"<b>{SECTION_HEADERS[cat]}</b>")
        for rel in items:
            lines.append(_format_line(rel))

    return _pack(lines)


def header_text(week_start: date, week_end: date) -> str:
    return f"🗓 Sorties du {_fr_date(week_start)} au {_fr_date(week_end)}"


def arr_button(rel: Release) -> dict | None:
    """Bouton inline (reply_markup) vers la page d'ajout Radarr/Sonarr, ou None."""
    if not rel.arr_url:
        return None
    label = "Sonarr" if rel.media_type == "tv" else "Radarr"
    return {"text": f"➕ {label}", "url": rel.arr_url}


def trailer_button(rel: Release) -> dict | None:
    """Bouton inline vers la bande-annonce YouTube, ou None."""
    if not rel.trailer_url:
        return None
    return {"text": "🎞 BA", "url": rel.trailer_url}


def cinema_button(rel: Release, label: str = "Séances") -> dict | None:
    """Bouton inline vers la recherche séances (sorties ciné), ou None."""
    if not rel.cinema_url:
        return None
    return {"text": f"🎟 {label}", "url": rel.cinema_url}


def item_buttons(rel: Release, cinema_label: str = "Séances") -> list[dict]:
    """Boutons inline d'un titre : séances ciné, bande-annonce, ajout *arr."""
    return [b for b in (cinema_button(rel, cinema_label),
                        trailer_button(rel), arr_button(rel)) if b]


def card_text(rel: Release, category: str) -> str:
    """Message d'un titre en mode carte : l'URL IMDb/TMDB est le 1er lien,
    donc Telegram déplie sa carte (affiche + note + genres)."""
    emoji, label = label_for(rel, category)
    title = _escape(rel.title[:200])
    year = f" ({rel.year})" if rel.year else ""
    # Lien invisible (zero-width) en tête : Telegram déplie sa carte sans
    # rendre le titre cliquable (le titre reste du texte simple).
    hidden = f'<a href="{rel.card_url}">&#8203;</a>'
    lines = [
        f'{hidden}<b>{emoji} {label}</b>',
        f'<b>{title}{year}</b>',
    ]
    meta = []
    sources = ", ".join(sorted(rel.sources))
    if sources:
        meta.append(_escape(sources))
    if rel.vote_average > 0:
        meta.append(f"⭐ {rel.vote_average:.1f}")
    if rel.rt_score is not None:
        meta.append(f"🍅 {rel.rt_score}%")
    if meta:
        lines.append(" · ".join(meta))
    # le lien d'ajout Radarr/Sonarr est un BOUTON inline (voir build_card_plan)
    return "\n".join(lines)


def build_card_plan(
    buckets: dict[str, list[Release]],
    week_start: date,
    week_end: date,
    categories: list[str],
    cinema_label: str = "Séances",
) -> list[dict]:
    """Plan en mode carte : en-tête + un message-carte par titre.

    Chaque action carte a preview=True (Telegram déplie l'affiche du lien).
    """
    header = header_text(week_start, week_end)
    total = sum(len(buckets.get(c, [])) for c in categories)
    if total == 0:
        return [{"kind": "text", "text": f"{header}\n\nAucune sortie détectée cette semaine."}]

    plan: list[dict] = [{"kind": "text", "text": header}]
    for cat in SECTION_ORDER:
        if cat not in categories:
            continue
        for rel in buckets.get(cat, []):
            action = {"kind": "text", "text": card_text(rel, cat), "preview": True}
            btns = item_buttons(rel, cinema_label)
            if btns:
                action["buttons"] = btns
            plan.append(action)
    return plan


def _pack(lines: list[str]) -> list[str]:
    """Regroupe les lignes en messages sans dépasser la limite Telegram."""
    messages: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        add = len(line) + (1 if current else 0)  # \n de jointure
        if current and size + add > TELEGRAM_LIMIT:
            messages.append("\n".join(current))
            current = [line]
            size = len(line)
        else:
            current.append(line)
            size += add
    if current:
        messages.append("\n".join(current))
    return messages

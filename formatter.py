"""Category sorting plus Telegram-safe HTML formatting and splitting."""
from __future__ import annotations

from datetime import date

from tmdb import Release

TELEGRAM_LIMIT = 4096

# Section display order and emoji headers.
SECTION_HEADERS = {
    "films": "🎬 Films",
    "series": "📺 Series",
    "animation": "🧸 Animation",
    "animation_series": "🧸📺 Animated series",
}
SECTION_ORDER = ["films", "series", "animation", "animation_series"]

# Emoji per category, reused for poster captions.
CATEGORY_EMOJI = {
    "films": "🎬",
    "series": "📺",
    "animation": "🧸",
    "animation_series": "🧸📺",
}

# Singular label (card mode, one message per title).
CATEGORY_LABEL = {
    "films": "Film",
    "series": "Series",
    "animation": "Animation",
    "animation_series": "Animated series",
}


def label_for(rel: Release, category: str) -> tuple[str, str]:
    """Return the (emoji, label) for a title. Highlights cinema releases (🍿)."""
    if rel.is_cinema and category in ("films", "animation"):
        emoji = "🍿"
        label = "Animation at the cinema" if category == "animation" else "At the cinema"
        return emoji, label
    return CATEGORY_EMOJI.get(category, "🎬"), CATEGORY_LABEL.get(category, "Film")


_MONTHS_EN = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def category_of(rel: Release) -> str:
    """Return a Release category based on media_type and Animation genre (16)."""
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
    """Group releases by category, filter, sort by popularity, and truncate.

    `min_popularity` filters out small productions / niche titles
    (TMDB popularity reflects current buzz, useful for fresh releases with
    little or no votes yet).
    Only the requested categories are produced.
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
        # Cinema releases first, then by descending popularity
        buckets[cat].sort(key=lambda r: (not r.is_cinema, -r.popularity))
        del buckets[cat][max_items:]
    return buckets


def _fr_date(d: date) -> str:
    return f"{d.day} {_MONTHS_EN[d.month]} {d.year}"


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
    """Build the list of messages (<4096 chars) ready for sendMessage.

    Splits between lines/sections, never in the middle of an HTML tag.
    """
    header = header_text(week_start, week_end)

    total = sum(len(buckets.get(c, [])) for c in categories)
    if total == 0:
        return [f"{header}\n\nNo releases detected this week."]

    # List of line blocks; each line is atomic and never split.
    lines: list[str] = [header]
    for cat in SECTION_ORDER:
        if cat not in categories:
            continue
        items = buckets.get(cat, [])
        if not items:
            continue
        lines.append("")  # separator
        lines.append(f"<b>{SECTION_HEADERS[cat]}</b>")
        for rel in items:
            lines.append(_format_line(rel))

    return _pack(lines)


def header_text(week_start: date, week_end: date) -> str:
    return f"🗓 Releases from {_fr_date(week_start)} to {_fr_date(week_end)}"


def arr_button(rel: Release) -> dict | None:
    """Inline button (reply_markup) to the Radarr/Sonarr add page, or None."""
    if not rel.arr_url:
        return None
    label = "Sonarr" if rel.media_type == "tv" else "Radarr"
    return {"text": f"➕ {label}", "url": rel.arr_url}


def trailer_button(rel: Release) -> dict | None:
    """Inline button to the YouTube trailer, or None."""
    if not rel.trailer_url:
        return None
    return {"text": "🎞 Trailer", "url": rel.trailer_url}


def cinema_button(rel: Release, label: str = "Showtimes") -> dict | None:
    """Inline button to the cinema showtimes search, or None."""
    if not rel.cinema_url:
        return None
    return {"text": f"🎟 {label}", "url": rel.cinema_url}


def item_buttons(rel: Release, cinema_label: str = "Showtimes") -> list[dict]:
    """Inline buttons for a title: cinema showtimes, trailer, and *arr add link."""
    return [b for b in (cinema_button(rel, cinema_label),
                        trailer_button(rel), arr_button(rel)) if b]


def card_text(rel: Release, category: str) -> str:
    """Card-mode message for a title: the IMDb/TMDB URL is the first link,
    so Telegram expands the preview card (poster, rating, genres)."""
    emoji, label = label_for(rel, category)
    title = _escape(rel.title[:200])
    year = f" ({rel.year})" if rel.year else ""
    # Invisible zero-width link at the top: Telegram expands the card without
    # making the title clickable (the title remains plain text).
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
    # The Radarr/Sonarr add link is an inline BUTTON (see build_card_plan)
    return "\n".join(lines)


def build_card_plan(
    buckets: dict[str, list[Release]],
    week_start: date,
    week_end: date,
    categories: list[str],
    cinema_label: str = "Showtimes",
) -> list[dict]:
    """Card-mode plan: header plus one card message per title.

    Each card action has preview=True (Telegram expands the link preview).
    """
    header = header_text(week_start, week_end)
    total = sum(len(buckets.get(c, [])) for c in categories)
    if total == 0:
        return [{"kind": "text", "text": f"{header}\n\nNo releases detected this week."}]

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
    """Group lines into messages without exceeding Telegram's limit."""
    messages: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        add = len(line) + (1 if current else 0)  # joining newline
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

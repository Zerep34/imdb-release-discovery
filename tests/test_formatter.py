"""Tests du formatage : classement genre 16, découpage <4096, cas vide."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import formatter as fmt  # noqa: E402
from tmdb import Release  # noqa: E402

WS = date(2026, 7, 13)
WE = date(2026, 7, 19)
CATS = ["films", "series", "animation", "animation_series"]


def mk(mt, tid, genres=(), pop=1.0, va=0.0, vc=0, title=None):
    return Release(
        media_type=mt, tmdb_id=tid, title=title or f"T{tid}",
        release_date="2026-07-15", year="2026",
        popularity=pop, vote_average=va, vote_count=vc, genre_ids=tuple(genres),
        sources={"Netflix (FR)"},
    )


def test_category_classification():
    rels = [
        mk("movie", 1),               # film
        mk("movie", 2, genres=(16,)),  # animation
        mk("tv", 3),                  # série
        mk("tv", 4, genres=(16,)),     # série d'animation
    ]
    b = fmt.classify(rels, CATS, max_items=15)
    assert [r.tmdb_id for r in b["films"]] == [1]
    assert [r.tmdb_id for r in b["animation"]] == [2]
    assert [r.tmdb_id for r in b["series"]] == [3]
    assert [r.tmdb_id for r in b["animation_series"]] == [4]


def test_only_requested_categories():
    rels = [mk("movie", 1), mk("tv", 3)]
    b = fmt.classify(rels, ["films"], max_items=15)
    assert set(b.keys()) == {"films"}


def test_sort_by_popularity_and_truncate():
    rels = [mk("movie", i, pop=float(i)) for i in range(1, 6)]
    b = fmt.classify(rels, ["films"], max_items=3)
    assert [r.tmdb_id for r in b["films"]] == [5, 4, 3]


def test_min_vote_count_filters():
    rels = [mk("movie", 1, vc=5), mk("movie", 2, vc=50)]
    b = fmt.classify(rels, ["films"], max_items=15, min_vote_count=10)
    assert [r.tmdb_id for r in b["films"]] == [2]


def test_min_popularity_filters_small_productions():
    rels = [mk("movie", 1, pop=3.0), mk("movie", 2, pop=25.0)]
    b = fmt.classify(rels, ["films"], max_items=15, min_popularity=10)
    assert [r.tmdb_id for r in b["films"]] == [2]


def test_empty_message():
    b = fmt.classify([], CATS, max_items=15)
    msgs = fmt.build_messages(b, WS, WE, CATS)
    assert len(msgs) == 1
    assert "Aucune sortie" in msgs[0]
    assert "13 juillet 2026" in msgs[0]


def test_html_escaping_and_link():
    rels = [mk("movie", 42, title="Tom & <Jerry>", va=7.4)]
    b = fmt.classify(rels, ["films"], max_items=15)
    msg = fmt.build_messages(b, WS, WE, ["films"])[0]
    assert "Tom &amp; &lt;Jerry&gt;" in msg
    assert "https://www.themoviedb.org/movie/42" in msg
    assert "⭐ 7.4" in msg


def test_arr_button_and_text_link():
    movie = mk("movie", 5)
    movie.arr_url = "http://192.168.1.1:7878/add/new?term=tmdb:5"
    tv = mk("tv", 6)
    tv.arr_url = "http://192.168.1.1:8989/add/new?term=tvdb:99"
    # bouton inline (card)
    bm = fmt.arr_button(movie)
    assert bm == {"text": "➕ Radarr", "url": movie.arr_url}
    assert fmt.arr_button(tv)["text"] == "➕ Sonarr"
    # la carte ne contient plus le lien arr en texte
    assert "➕" not in fmt.card_text(movie, "films")
    # le mode texte groupé garde le lien en ligne
    assert "➕ Radarr" in fmt._format_line(movie)


def test_no_arr_button_when_absent():
    assert fmt.arr_button(mk("movie", 1)) is None
    assert "➕" not in fmt._format_line(mk("movie", 1))


def test_card_plan_attaches_buttons():
    m = mk("movie", 5)
    m.arr_url = "http://192.168.1.1:7878/add/new?term=tmdb:5"
    b = fmt.classify([m], ["films"], max_items=15)
    plan = fmt.build_card_plan(b, WS, WE, ["films"])
    card = [a for a in plan if a.get("preview")][0]
    assert card["buttons"] == [{"text": "➕ Radarr", "url": m.arr_url}]


def test_trailer_button_and_order():
    m = mk("movie", 5)
    m.trailer_url = "https://www.youtube.com/watch?v=abc"
    m.arr_url = "http://192.168.1.1:7878/add/new?term=tmdb:5"
    btns = fmt.item_buttons(m)
    # bande-annonce d'abord, ajout ensuite
    assert [b["text"] for b in btns] == ["🎞 BA", "➕ Radarr"]
    assert fmt.trailer_button(m)["url"] == "https://www.youtube.com/watch?v=abc"


def test_no_trailer_button_when_absent():
    assert fmt.trailer_button(mk("movie", 1)) is None
    assert fmt.item_buttons(mk("movie", 1)) == []


def test_rt_score_rendered_when_present():
    m = mk("movie", 1, va=7.0)
    m.rt_score = 83
    assert "🍅 83%" in fmt.card_text(m, "films")
    assert "🍅 83%" in fmt._format_line(m)


def test_rt_score_omitted_when_none():
    m = mk("movie", 1)
    assert "🍅" not in fmt.card_text(m, "films")
    assert "🍅" not in fmt._format_line(m)


def test_rt_score_zero_still_shown():
    m = mk("movie", 1)
    m.rt_score = 0
    assert "🍅 0%" in fmt._format_line(m)


def test_card_uses_imdb_url_as_first_link():
    m = mk("movie", 1, va=6.3)
    m.imdb_id = "tt31728330"
    m.rt_score = 78
    txt = fmt.card_text(m, "films")
    # lien invisible IMDb en tête (Telegram déplie cette carte)
    assert 'href="https://www.imdb.com/title/tt31728330/"' in txt
    assert txt.startswith('<a href="https://www.imdb.com/title/tt31728330/">')
    assert "🎬 Film" in txt
    assert "⭐ 6.3" in txt and "🍅 78%" in txt


def test_card_falls_back_to_tmdb_when_no_imdb():
    m = mk("movie", 42)
    assert "https://www.themoviedb.org/movie/42" in fmt.card_text(m, "films")


def test_card_plan_one_message_per_release_with_preview():
    rels = [mk("movie", 1), mk("tv", 2)]
    b = fmt.classify(rels, ["films", "series"], max_items=15)
    plan = fmt.build_card_plan(b, WS, WE, ["films", "series"])
    cards = [a for a in plan if a.get("preview")]
    assert len(cards) == 2
    assert plan[0]["kind"] == "text" and "Sorties du" in plan[0]["text"]


def test_card_plan_empty():
    b = fmt.classify([], CATS, max_items=15)
    plan = fmt.build_card_plan(b, WS, WE, CATS)
    assert len(plan) == 1 and "Aucune sortie" in plan[0]["text"]


def _cine(mt, tid, **kw):
    r = mk(mt, tid, **kw)
    r.sources = {"Cinéma (FR)"}
    return r


def test_cinema_label_and_emoji():
    c = _cine("movie", 1)
    assert fmt.label_for(c, "films") == ("🍿", "Au cinéma")
    assert fmt.label_for(c, "animation") == ("🍿", "Animation au cinéma")
    # streaming garde le label normal
    assert fmt.label_for(mk("movie", 2), "films") == ("🎬", "Film")


def test_cinema_highlighted_in_card_and_line():
    c = _cine("movie", 1)
    assert "🍿 Au cinéma" in fmt.card_text(c, "films")
    assert fmt._format_line(c).startswith("🍿 ")
    # streaming = puce normale
    assert fmt._format_line(mk("movie", 2)).startswith("• ")


def test_cinema_sorted_first():
    stream = mk("movie", 1, pop=99.0)          # streaming très populaire
    cine = _cine("movie", 2, pop=1.0)          # ciné peu populaire
    b = fmt.classify([stream, cine], ["films"], max_items=15)
    assert [r.tmdb_id for r in b["films"]] == [2, 1]  # ciné en tête malgré popularité


def test_cinema_button_only_for_cinema():
    c = _cine("movie", 1)
    c.cinema_url = "https://www.allocine.fr/rechercher/?q=Superman"
    assert fmt.cinema_button(c, "UGC") == {"text": "🎟 UGC", "url": c.cinema_url}
    # pas d'URL ciné -> pas de bouton
    assert fmt.cinema_button(mk("movie", 2)) is None


def test_item_buttons_order_cinema_first():
    c = _cine("movie", 1)
    c.cinema_url = "https://allocine/x"
    c.trailer_url = "https://youtu.be/x"
    c.arr_url = "http://192.168.1.1:7878/add/new?term=tmdb:1"
    labels = [b["text"] for b in fmt.item_buttons(c, "Séances")]
    assert labels == ["🎟 Séances", "🎞 BA", "➕ Radarr"]


def test_chunking_under_limit_no_broken_tags():
    # beaucoup d'items pour forcer plusieurs messages
    rels = [mk("movie", i, pop=float(i), va=6.0, title=f"Titre {i} " * 5)
            for i in range(1, 400)]
    b = fmt.classify(rels, ["films"], max_items=1000)
    msgs = fmt.build_messages(b, WS, WE, ["films"])
    assert len(msgs) > 1
    for m in msgs:
        assert len(m) <= fmt.TELEGRAM_LIMIT
        # pas de balise <a coupée : autant d'ouvertures que de fermetures
        assert m.count("<a ") == m.count("</a>")
        assert m.count("<b>") == m.count("</b>")

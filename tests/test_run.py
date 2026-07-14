"""Tests d'orchestration run() : styles card/text, dry-run vs envoi, historique."""
import argparse
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import releases_to_telegram as rt  # noqa: E402
from tmdb import Release  # noqa: E402


def _rel(tid=1):
    return Release(
        media_type="movie", tmdb_id=tid, title=f"T{tid}",
        release_date="2026-07-15", year="2026", popularity=1.0,
        vote_average=7.0, vote_count=10, genre_ids=(), sources={"Cinéma (FR)"},
    )


def _args(**kw):
    base = dict(week="current", dry_run=False, ignore_history=True, text=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _cfg(**kw):
    c = dict(rt.DEFAULTS)
    c.update(tmdb_api_key="x", telegram_bot_token="x", telegram_chat_id="@c",
             use_history=False)
    c.update(kw)
    return c


class FakeTelegram:
    instances = []

    def __init__(self, token, chat):
        self.sent_plans = []
        self.sent_msgs = []
        FakeTelegram.instances.append(self)

    def send_plan(self, plan):
        self.sent_plans.append(plan)

    def send_all(self, messages):
        self.sent_msgs.append(messages)


def _patch(monkeypatch, releases):
    monkeypatch.setattr(rt, "TMDBClient", lambda *a, **k: types.SimpleNamespace())
    monkeypatch.setattr(rt, "collect", lambda c, cfg, ws, we: releases)
    monkeypatch.setattr(rt, "enrich", lambda *a, **k: None)
    FakeTelegram.instances = []
    monkeypatch.setattr(rt, "TelegramClient", FakeTelegram)


def test_run_card_dry_run_prints_and_sends_nothing(monkeypatch, capsys):
    _patch(monkeypatch, [_rel()])
    rc = rt.run(_cfg(style="card"), _args(dry_run=True))
    assert rc == rt.EXIT_OK
    assert FakeTelegram.instances == []  # rien construit/envoyé
    assert "DRY-RUN (card)" in capsys.readouterr().out


def test_run_text_dry_run(monkeypatch, capsys):
    _patch(monkeypatch, [_rel()])
    rc = rt.run(_cfg(style="text"), _args(dry_run=True, text=True))
    assert rc == rt.EXIT_OK
    assert FakeTelegram.instances == []
    assert "DRY-RUN" in capsys.readouterr().out


def test_run_card_sends_plan(monkeypatch):
    _patch(monkeypatch, [_rel()])
    rc = rt.run(_cfg(style="card"), _args())
    assert rc == rt.EXIT_OK
    assert len(FakeTelegram.instances) == 1
    tg = FakeTelegram.instances[0]
    assert len(tg.sent_plans) == 1 and tg.sent_msgs == []
    assert tg.sent_plans[0][0]["kind"] == "text"  # en-tête


def test_run_text_sends_messages(monkeypatch):
    _patch(monkeypatch, [_rel()])
    rc = rt.run(_cfg(style="text"), _args(text=True))
    assert rc == rt.EXIT_OK
    tg = FakeTelegram.instances[0]
    assert tg.sent_msgs and tg.sent_plans == []


def test_run_text_flag_overrides_card_config(monkeypatch):
    _patch(monkeypatch, [_rel()])
    rt.run(_cfg(style="card"), _args(text=True))  # --text l'emporte sur style=card
    tg = FakeTelegram.instances[0]
    assert tg.sent_msgs and tg.sent_plans == []


def test_run_saves_history_after_send(monkeypatch, tmp_path):
    _patch(monkeypatch, [_rel()])
    saved = {}
    monkeypatch.setattr(rt.history, "load", lambda p: set())
    monkeypatch.setattr(rt.history, "prune", lambda b, s: ["k1"])
    monkeypatch.setattr(rt.history, "save", lambda p, keys: saved.update(keys=keys))
    cfg = _cfg(style="card", use_history=True, history_file=str(tmp_path / "h.json"))
    rt.run(cfg, _args(ignore_history=False))
    assert saved["keys"] == {"k1"}


def test_run_dry_run_does_not_save_history(monkeypatch, tmp_path):
    _patch(monkeypatch, [_rel()])
    calls = []
    monkeypatch.setattr(rt.history, "load", lambda p: set())
    monkeypatch.setattr(rt.history, "prune", lambda b, s: ["k1"])
    monkeypatch.setattr(rt.history, "save", lambda p, keys: calls.append(keys))
    cfg = _cfg(style="card", use_history=True, history_file=str(tmp_path / "h.json"))
    rt.run(cfg, _args(dry_run=True, ignore_history=False))
    assert calls == []  # aucun save en dry-run

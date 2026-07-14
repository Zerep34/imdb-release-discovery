"""Tests de la fenêtre : semaine de 7 jours ancrée (défaut mercredi=2)."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from releases_to_telegram import week_window  # noqa: E402


def test_current_default_wednesday_anchor():
    # samedi 11 juillet 2026 -> semaine mercredi 08 -> mardi 14
    start, end = week_window("current", "Europe/Paris", today=date(2026, 7, 11))
    assert start == date(2026, 7, 8)    # mercredi
    assert end == date(2026, 7, 14)     # mardi
    assert start.weekday() == 2
    assert end.weekday() == 1


def test_current_on_wednesday_starts_today():
    start, end = week_window("current", "Europe/Paris", today=date(2026, 7, 8))
    assert start == date(2026, 7, 8)
    assert end == date(2026, 7, 14)


def test_current_on_tuesday_is_last_day():
    start, end = week_window("current", "Europe/Paris", today=date(2026, 7, 14))
    assert start == date(2026, 7, 8)
    assert end == date(2026, 7, 14)


def test_next_and_last():
    nstart, nend = week_window("next", "Europe/Paris", today=date(2026, 7, 11))
    assert nstart == date(2026, 7, 15)
    assert nend == date(2026, 7, 21)

    lstart, lend = week_window("last", "Europe/Paris", today=date(2026, 7, 11))
    assert lstart == date(2026, 7, 1)
    assert lend == date(2026, 7, 7)


def test_span_is_seven_days():
    start, end = week_window("current", "Europe/Paris", today=date(2026, 1, 1))
    assert (end - start).days == 6


def test_configurable_start_day_monday():
    # start_day=0 -> lundi->dimanche
    start, end = week_window("current", "Europe/Paris",
                             today=date(2026, 7, 11), start_day=0)
    assert start == date(2026, 7, 6)    # lundi
    assert end == date(2026, 7, 12)     # dimanche


def test_default_today_uses_timezone():
    start, end = week_window("current", "Asia/Tokyo")
    assert start.weekday() == 2   # mercredi
    assert end.weekday() == 1     # mardi

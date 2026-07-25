#!/usr/bin/env python3
"""Unit tests for scripts/set_watch.py — conditional watches position_stream.py
mechanically executes on Stan's behalf. No network calls; WATCHES_PATH is
monkeypatched to a tmp_path per test."""
import argparse
import datetime
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import set_watch  # noqa: E402

ET = ZoneInfo("America/New_York")
# A fixed Wednesday 12:00 ET, well before market close, so tests don't flake
# depending on when they run.
FIXED_NOW = datetime.datetime(2026, 7, 22, 12, 0, tzinfo=ET)


@pytest.fixture
def watches_file(tmp_path, monkeypatch):
    path = tmp_path / "watches.json"
    monkeypatch.setattr(set_watch, "STATE_DIR", tmp_path)
    monkeypatch.setattr(set_watch, "WATCHES_PATH", path)
    return path


@pytest.fixture
def frozen_now(monkeypatch):
    monkeypatch.setattr(set_watch, "_now_et", lambda: FIXED_NOW)
    return FIXED_NOW


# After market close, same Wednesday. Regression fixture for the real bug
# found via a live CLI smoke test: "default to today's close" produced an
# already-past timestamp for any SELL/ALERT set after 16:00 ET.
FIXED_AFTER_HOURS = datetime.datetime(2026, 7, 22, 22, 17, tzinfo=ET)
FIXED_WEEKEND = datetime.datetime(2026, 7, 25, 12, 0, tzinfo=ET)  # a Saturday


@pytest.fixture
def frozen_after_hours(monkeypatch):
    monkeypatch.setattr(set_watch, "_now_et", lambda: FIXED_AFTER_HOURS)
    return FIXED_AFTER_HOURS


def _add_args(**overrides):
    defaults = dict(
        ticker="ip", field="price", comparator=">=", value=45.0,
        action="SELL", qty="all", conviction=None, sector=None,
        reason="test", expires_at=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


class TestMarketIsOpen:
    def test_weekday_market_hours_open(self):
        assert set_watch._market_is_open(FIXED_NOW) is True

    def test_weekday_after_close_is_closed(self):
        assert set_watch._market_is_open(FIXED_AFTER_HOURS) is False

    def test_weekend_is_closed(self):
        assert set_watch._market_is_open(FIXED_WEEKEND) is False

    def test_before_open_is_closed(self):
        before_open = FIXED_NOW.replace(hour=8, minute=0)
        assert set_watch._market_is_open(before_open) is False


class TestValidateAdd:
    def test_sell_all_defaults_to_market_close(self, frozen_now):
        watch, error = set_watch._validate_add(_add_args())
        assert error is None
        assert watch["ticker"] == "IP"
        assert watch["expires_at"] == FIXED_NOW.replace(hour=16, minute=0, second=0, microsecond=0).isoformat()

    def test_sell_off_hours_defaults_to_short_window_not_past_close(self, frozen_after_hours):
        watch, error = set_watch._validate_add(_add_args())
        assert error is None, f"off-hours add should succeed, got: {error}"
        expires = datetime.datetime.fromisoformat(watch["expires_at"])
        assert expires == FIXED_AFTER_HOURS + datetime.timedelta(seconds=set_watch.OFF_HOURS_DEFAULT_TTL_SECONDS)

    def test_alert_off_hours_defaults_to_short_window(self, frozen_after_hours):
        watch, error = set_watch._validate_add(_add_args(action="ALERT", qty=None, comparator="<=", value=1.0))
        assert error is None
        expires = datetime.datetime.fromisoformat(watch["expires_at"])
        assert expires == FIXED_AFTER_HOURS + datetime.timedelta(seconds=set_watch.OFF_HOURS_DEFAULT_TTL_SECONDS)

    def test_weekend_defaults_to_short_window(self, monkeypatch):
        monkeypatch.setattr(set_watch, "_now_et", lambda: FIXED_WEEKEND)
        watch, error = set_watch._validate_add(_add_args())
        assert error is None
        expires = datetime.datetime.fromisoformat(watch["expires_at"])
        assert expires == FIXED_WEEKEND + datetime.timedelta(seconds=set_watch.OFF_HOURS_DEFAULT_TTL_SECONDS)

    def test_sell_off_hours_explicit_expiry_beyond_cap_rejected(self, frozen_after_hours):
        requested = (FIXED_AFTER_HOURS + datetime.timedelta(hours=3)).isoformat()
        _, error = set_watch._validate_add(_add_args(expires_at=requested))
        assert "capped to a short off-hours window" in error

    def test_sell_qty_must_be_positive_int_or_all(self, frozen_now):
        _, error = set_watch._validate_add(_add_args(qty="-3"))
        assert "positive" in error

    def test_sell_qty_zero_rejected(self, frozen_now):
        _, error = set_watch._validate_add(_add_args(qty="0"))
        assert "positive" in error

    def test_buy_requires_conviction(self, frozen_now):
        watch, error = set_watch._validate_add(
            _add_args(action="BUY", qty="3", comparator="<=", value=28.50)
        )
        assert watch is None
        assert "conviction" in error

    def test_buy_requires_concrete_qty_not_all(self, frozen_now):
        _, error = set_watch._validate_add(
            _add_args(action="BUY", qty="all", conviction=0.6, comparator="<=", value=28.50)
        )
        assert "no 'all'" in error

    def test_buy_conviction_out_of_range_rejected(self, frozen_now):
        _, error = set_watch._validate_add(
            _add_args(action="BUY", qty="3", conviction=1.5, comparator="<=", value=28.50)
        )
        assert "between 0 and 1" in error

    def test_buy_default_expiry_is_five_minutes(self, frozen_now):
        watch, error = set_watch._validate_add(
            _add_args(action="BUY", qty="3", conviction=0.6, comparator="<=", value=28.50)
        )
        assert error is None
        expires = datetime.datetime.fromisoformat(watch["expires_at"])
        assert expires == FIXED_NOW + datetime.timedelta(minutes=5)

    def test_buy_expiry_beyond_five_minutes_rejected(self, frozen_now):
        requested = (FIXED_NOW + datetime.timedelta(minutes=30)).isoformat()
        _, error = set_watch._validate_add(
            _add_args(action="BUY", qty="3", conviction=0.6, comparator="<=", value=28.50,
                      expires_at=requested)
        )
        assert "5 minutes" in error

    def test_buy_expiry_within_five_minutes_accepted(self, frozen_now):
        requested = (FIXED_NOW + datetime.timedelta(minutes=2)).isoformat()
        watch, error = set_watch._validate_add(
            _add_args(action="BUY", qty="3", conviction=0.6, comparator="<=", value=28.50,
                      expires_at=requested)
        )
        assert error is None
        assert watch["expires_at"] == requested

    def test_sell_expiry_beyond_market_close_rejected(self, frozen_now):
        requested = (FIXED_NOW + datetime.timedelta(days=1)).isoformat()
        _, error = set_watch._validate_add(_add_args(expires_at=requested))
        assert "can't outlive today's session" in error

    def test_alert_needs_no_qty_or_conviction(self, frozen_now):
        watch, error = set_watch._validate_add(
            _add_args(action="ALERT", qty=None, comparator="<=", value=13.50)
        )
        assert error is None
        assert watch["qty"] is None
        assert watch["conviction"] is None

    def test_invalid_field_rejected(self, frozen_now):
        _, error = set_watch._validate_add(_add_args(field="macdh"))
        assert "field" in error

    def test_invalid_comparator_rejected(self, frozen_now):
        _, error = set_watch._validate_add(_add_args(comparator="=="))
        assert "comparator" in error

    def test_invalid_action_rejected(self, frozen_now):
        _, error = set_watch._validate_add(_add_args(action="HOLD"))
        assert "action" in error

    def test_expiry_in_the_past_rejected(self, frozen_now):
        requested = (FIXED_NOW - datetime.timedelta(minutes=1)).isoformat()
        _, error = set_watch._validate_add(_add_args(expires_at=requested))
        assert "future" in error

    def test_each_watch_gets_a_unique_id(self, frozen_now):
        w1, _ = set_watch._validate_add(_add_args())
        w2, _ = set_watch._validate_add(_add_args())
        assert w1["id"] != w2["id"]


class TestPruneExpired:
    def test_expired_watch_pruned(self, frozen_now):
        expired = {"id": "a", "ticker": "IP", "expires_at": (FIXED_NOW - datetime.timedelta(minutes=1)).isoformat()}
        active = {"id": "b", "ticker": "IP", "expires_at": (FIXED_NOW + datetime.timedelta(minutes=1)).isoformat()}
        kept = set_watch.prune_expired([expired, active])
        assert kept == [active]

    def test_malformed_entry_dropped(self, frozen_now):
        malformed = {"id": "a", "ticker": "IP"}  # no expires_at
        kept = set_watch.prune_expired([malformed])
        assert kept == []


class TestLocking:
    def test_lock_file_created_under_patched_state_dir_not_real_one(self, watches_file, frozen_now):
        with set_watch._locked():
            pass
        assert (watches_file.parent / "watches.json.lock").exists()

    def test_locked_is_reentrant_safe_sequentially(self, watches_file, frozen_now):
        # Not concurrency itself (flock's OS-level exclusion isn't practical
        # to assert in-process) - just that two sequential uses both complete
        # without deadlocking on the same lock file.
        with set_watch._locked():
            set_watch.save_watches([{"id": "a"}])
        with set_watch._locked():
            watches = set_watch.load_watches()
        assert watches == [{"id": "a"}]


class TestLoadSaveRoundtrip:
    def test_save_then_load(self, watches_file):
        watches = [{"id": "a", "ticker": "IP"}]
        set_watch.save_watches(watches)
        assert set_watch.load_watches() == watches

    def test_load_missing_file_returns_empty(self, watches_file):
        assert set_watch.load_watches() == []

    def test_load_malformed_json_returns_empty(self, watches_file):
        watches_file.parent.mkdir(exist_ok=True)
        watches_file.write_text("not json")
        assert set_watch.load_watches() == []


class TestMainAddListClear:
    def test_add_persists_and_list_shows_it(self, watches_file, frozen_now, capsys):
        sys.argv = ["set_watch.py", "add", "--ticker", "ip", "--field", "price",
                    "--comparator", ">=", "--value", "45.0", "--action", "SELL",
                    "--qty", "all", "--reason", "test"]
        assert set_watch.main() == 0
        capsys.readouterr()

        sys.argv = ["set_watch.py", "list"]
        assert set_watch.main() == 0
        out = json.loads(capsys.readouterr().out)
        assert len(out["watches"]) == 1
        assert out["watches"][0]["ticker"] == "IP"

    def test_add_invalid_exits_nonzero_and_does_not_persist(self, watches_file, frozen_now, capsys):
        sys.argv = ["set_watch.py", "add", "--ticker", "ip", "--field", "bogus",
                    "--comparator", ">=", "--value", "45.0", "--action", "SELL",
                    "--qty", "all", "--reason", "test"]
        assert set_watch.main() == 1
        assert set_watch.load_watches() == []

    def test_clear_by_id(self, watches_file, frozen_now, capsys):
        sys.argv = ["set_watch.py", "add", "--ticker", "ip", "--field", "price",
                    "--comparator", ">=", "--value", "45.0", "--action", "SELL",
                    "--qty", "all", "--reason", "test"]
        set_watch.main()
        watch_id = json.loads(capsys.readouterr().out)["watch"]["id"]

        sys.argv = ["set_watch.py", "clear", "--id", watch_id]
        assert set_watch.main() == 0
        assert set_watch.load_watches() == []

    def test_clear_by_ticker(self, watches_file, frozen_now, capsys):
        for _ in range(2):
            sys.argv = ["set_watch.py", "add", "--ticker", "ip", "--field", "price",
                        "--comparator", ">=", "--value", "45.0", "--action", "SELL",
                        "--qty", "all", "--reason", "test"]
            set_watch.main()
            capsys.readouterr()

        sys.argv = ["set_watch.py", "clear", "--ticker", "ip"]
        assert set_watch.main() == 0
        assert set_watch.load_watches() == []

    def test_clear_without_id_or_ticker_errors(self, watches_file, frozen_now, capsys):
        sys.argv = ["set_watch.py", "clear"]
        assert set_watch.main() == 1

#!/usr/bin/env python3
"""
Unit tests for scripts/edgar_scan.py -- the free SEC EDGAR corporate-
actions signal (BWMN data gap fix). Network is monkeypatched at the
isolated _fetch_*_raw() boundary functions, matching discovery_daemon.py's
_fetch_fundamentals() test convention -- no real requests.get calls here.
"""
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import edgar_scan  # noqa: E402


class TestGetTickerCikMap:
    def test_fetches_and_caches_when_missing(self, tmp_path):
        path = tmp_path / "map.json"
        result = edgar_scan.get_ticker_cik_map(
            now="2026-08-13T12:00:00+00:00", path=path,
            fetch_fn=lambda: {"AAA": "1234567", "BBB": "7654321"},
        )
        assert result == {"AAA": "1234567", "BBB": "7654321"}
        saved = json.loads(path.read_text())
        assert saved["AAA"] == "1234567"
        assert saved["_fetched_at"] == "2026-08-13T12:00:00+00:00"

    def test_uses_cache_when_fresh(self, tmp_path):
        path = tmp_path / "map.json"
        path.write_text(json.dumps({"AAA": "1234567", "_fetched_at": "2026-08-13T00:00:00+00:00"}))
        calls = []
        result = edgar_scan.get_ticker_cik_map(
            now="2026-08-13T01:00:00+00:00", path=path,
            fetch_fn=lambda: calls.append(1) or {"SHOULD": "NOT_BE_USED"},
        )
        assert result == {"AAA": "1234567"}
        assert calls == []

    def test_refetches_when_stale(self, tmp_path):
        path = tmp_path / "map.json"
        path.write_text(json.dumps({"OLD": "111", "_fetched_at": "2026-08-01T00:00:00+00:00"}))
        result = edgar_scan.get_ticker_cik_map(
            now="2026-08-13T00:00:00+00:00", path=path,
            fetch_fn=lambda: {"NEW": "222"},
        )
        assert result == {"NEW": "222"}

    def test_fetch_failure_falls_back_to_stale_cache(self, tmp_path):
        path = tmp_path / "map.json"
        path.write_text(json.dumps({"OLD": "111", "_fetched_at": "2026-08-01T00:00:00+00:00"}))

        def boom():
            raise ConnectionError("sec.gov unreachable")
        result = edgar_scan.get_ticker_cik_map(now="2026-08-13T00:00:00+00:00", path=path, fetch_fn=boom)
        assert result == {"OLD": "111"}

    def test_fetch_failure_with_no_cache_returns_empty(self, tmp_path):
        path = tmp_path / "map.json"

        def boom():
            raise ConnectionError("sec.gov unreachable")
        result = edgar_scan.get_ticker_cik_map(now="2026-08-13T00:00:00+00:00", path=path, fetch_fn=boom)
        assert result == {}


class TestCheckTickerForMaFiling:
    CIK_MAP = {"AAA": "1234567"}

    def _submissions(self, forms, dates, items, accessions=None, primary_docs=None):
        n = len(forms)
        return {"filings": {"recent": {
            "form": forms, "filingDate": dates, "items": items,
            "accessionNumber": accessions or [f"000000-26-{i:06d}" for i in range(n)],
            "primaryDocument": primary_docs or [f"doc{i}.htm" for i in range(n)],
        }}}

    def test_unknown_ticker_returns_empty(self):
        assert edgar_scan.check_ticker_for_ma_filing("ZZZ", {}, now="2026-08-13T00:00:00+00:00") == []

    def test_flags_recent_8k_with_ma_item_code(self):
        submissions = self._submissions(
            forms=["8-K"], dates=["2026-08-11"], items=["2.01,9.01"],
        )
        result = edgar_scan.check_ticker_for_ma_filing(
            "AAA", self.CIK_MAP, lookback_days=7, now="2026-08-13T00:00:00+00:00",
            fetch_fn=lambda cik: submissions,
        )
        assert len(result) == 1
        assert result[0]["ticker"] == "AAA"
        assert result[0]["items"] == ["2.01"]
        assert "sec.gov" in result[0]["filing_url"]

    def test_ignores_8k_without_ma_item_code(self):
        submissions = self._submissions(forms=["8-K"], dates=["2026-08-11"], items=["7.01"])
        result = edgar_scan.check_ticker_for_ma_filing(
            "AAA", self.CIK_MAP, now="2026-08-13T00:00:00+00:00", fetch_fn=lambda cik: submissions,
        )
        assert result == []

    def test_ignores_non_8k_forms(self):
        submissions = self._submissions(forms=["10-Q"], dates=["2026-08-11"], items=["2.01"])
        result = edgar_scan.check_ticker_for_ma_filing(
            "AAA", self.CIK_MAP, now="2026-08-13T00:00:00+00:00", fetch_fn=lambda cik: submissions,
        )
        assert result == []

    def test_ignores_filings_outside_lookback_window(self):
        submissions = self._submissions(forms=["8-K"], dates=["2026-07-01"], items=["2.01"])
        result = edgar_scan.check_ticker_for_ma_filing(
            "AAA", self.CIK_MAP, lookback_days=7, now="2026-08-13T00:00:00+00:00",
            fetch_fn=lambda cik: submissions,
        )
        assert result == []

    def test_network_failure_returns_empty_not_raises(self):
        def boom(cik):
            raise ConnectionError("sec.gov unreachable")
        result = edgar_scan.check_ticker_for_ma_filing(
            "AAA", self.CIK_MAP, now="2026-08-13T00:00:00+00:00", fetch_fn=boom,
        )
        assert result == []

    def test_malformed_response_returns_empty_not_raises(self):
        result = edgar_scan.check_ticker_for_ma_filing(
            "AAA", self.CIK_MAP, now="2026-08-13T00:00:00+00:00", fetch_fn=lambda cik: {"garbage": True},
        )
        assert result == []

    def test_matches_multiple_ma_item_codes(self):
        submissions = self._submissions(forms=["8-K"], dates=["2026-08-11"], items=["1.01,5.01"])
        result = edgar_scan.check_ticker_for_ma_filing(
            "AAA", self.CIK_MAP, now="2026-08-13T00:00:00+00:00", fetch_fn=lambda cik: submissions,
        )
        assert result[0]["items"] == ["1.01", "5.01"]


class TestScanTickers:
    def test_one_bad_ticker_does_not_block_the_rest(self, monkeypatch):
        """Defense-in-depth: even if check_ticker_for_ma_filing somehow
        raised (it shouldn't -- see TestCheckTickerForMaFiling -- but a
        future change could break that), scan_tickers' own try/except
        keeps the batch alive."""
        cik_map = {"GOOD": "111", "BAD": "222"}

        def fake_check(ticker, cik_map, lookback_days=7, now=None, fetch_fn=None):
            if ticker == "BAD":
                raise ConnectionError("simulated internal-isolation regression")
            return [{"ticker": "GOOD", "form": "8-K", "filing_date": "2026-08-11",
                      "items": ["2.01"], "accession_number": "x", "filing_url": "https://sec.gov/x"}]
        monkeypatch.setattr(edgar_scan, "check_ticker_for_ma_filing", fake_check)

        result = edgar_scan.scan_tickers(["BAD", "GOOD"], cik_map=cik_map, sleep_between_seconds=0)
        assert list(result.keys()) == ["GOOD"]

    def test_only_returns_tickers_with_flags(self, monkeypatch):
        def fake_check(ticker, cik_map, lookback_days=7, now=None, fetch_fn=None):
            return [{"ticker": ticker}] if ticker == "FLAGGED" else []
        monkeypatch.setattr(edgar_scan, "check_ticker_for_ma_filing", fake_check)
        result = edgar_scan.scan_tickers(["FLAGGED", "QUIET"], cik_map={}, sleep_between_seconds=0)
        assert list(result.keys()) == ["FLAGGED"]


class TestFetchRawBoundaries:
    """Confirms the network boundary functions call requests.get with the
    right URL/headers -- the actual isolation unit tests above never touch
    these, so this is what would catch a URL/header regression."""

    def test_ticker_cik_map_sends_required_user_agent(self, monkeypatch):
        captured = {}

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"0": {"cik_str": 123, "ticker": "AAA", "title": "AAA Inc"}}

        def fake_get(url, headers=None, timeout=None):
            captured["url"] = url
            captured["headers"] = headers
            return FakeResp()
        monkeypatch.setattr(edgar_scan.requests, "get", fake_get)

        result = edgar_scan._fetch_ticker_cik_map_raw()
        assert result == {"AAA": "123"}
        assert captured["url"] == "https://www.sec.gov/files/company_tickers.json"
        assert "User-Agent" in captured["headers"]
        assert "@" in captured["headers"]["User-Agent"]  # SEC requires a real contact, not a browser UA

    def test_submissions_url_is_zero_padded_cik(self, monkeypatch):
        captured = {}

        class FakeResp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"filings": {"recent": {}}}

        def fake_get(url, headers=None, timeout=None):
            captured["url"] = url
            return FakeResp()
        monkeypatch.setattr(edgar_scan.requests, "get", fake_get)

        edgar_scan._fetch_submissions_raw("320193")
        assert captured["url"] == "https://data.sec.gov/submissions/CIK0000320193.json"

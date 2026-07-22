#!/usr/bin/env python3
"""
Unit tests for scripts/news_collector.py's extract_tickers().

Regression tests for a real false-match bug fixed 2026-07-22 (commit
af13b19): extract_tickers() used to match ticker "F" out of "F-Secure Oyj"
(hyphen-adjacent) and out of "e.l.f." (period-split into E/L/F). The fix
adds a letter/period/hyphen lookaround so adjacency to those characters
excludes a match, while legitimate parenthetical/standalone/dotted mentions
still match.

`requests` (imported at module level by news_collector.py) is available in
this environment, so the module imports cleanly — no import-skip needed.
"""
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import news_collector  # noqa: E402


class TestExtractTickersRegressions:
    """Exact regression cases for the 2026-07-22 false-match fix."""

    def test_hyphenated_company_name_no_false_match(self):
        # "F-Secure" must not false-match ticker "F" via the hyphen.
        text = "F-Secure Oyj reported new malware research findings today."
        result = news_collector.extract_tickers(text, news_collector.KNOWN_TICKERS)
        assert result == []

    def test_dotted_company_name_no_false_match(self):
        # "e.l.f." splits into E/L/F via periods; none should match.
        text = "e.l.f. Beauty reported strong quarterly sales growth."
        result = news_collector.extract_tickers(text, news_collector.KNOWN_TICKERS)
        assert result == []


class TestExtractTickersLegitimateMatches:
    """Confirm the fix didn't break real matches."""

    def test_parenthetical_ticker_matches(self):
        text = "Ford (F) reported earnings this morning."
        result = news_collector.extract_tickers(text, news_collector.KNOWN_TICKERS)
        assert result == ["F"]

    def test_standalone_ticker_matches(self):
        text = "NVDA up 5% in after-hours trading."
        result = news_collector.extract_tickers(text, news_collector.KNOWN_TICKERS)
        assert result == ["NVDA"]

    def test_dotted_ticker_matches(self):
        text = "BRK.A trading higher after the earnings call."
        known = {"BRK.A"}
        result = news_collector.extract_tickers(text, known)
        assert result == ["BRK.A"]


class TestExtractTickersEdgeCases:
    def test_empty_text_returns_empty_list(self):
        assert news_collector.extract_tickers("", news_collector.KNOWN_TICKERS) == []

    def test_no_known_tickers_in_text_returns_empty(self):
        text = "The weather today is sunny with a slight breeze."
        assert news_collector.extract_tickers(text, news_collector.KNOWN_TICKERS) == []

    def test_deduplicates_repeated_mentions(self):
        text = "NVDA rallied. Later, NVDA gave back gains before NVDA closed flat."
        result = news_collector.extract_tickers(text, news_collector.KNOWN_TICKERS)
        assert result == ["NVDA"]

    def test_multiple_distinct_tickers_preserve_first_seen_order(self):
        text = "SOFI and PLTR both moved on the same catalyst headline."
        result = news_collector.extract_tickers(text, news_collector.KNOWN_TICKERS)
        assert result == ["SOFI", "PLTR"]

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
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import news_collector  # noqa: E402
import trader_db  # noqa: E402


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


class TestMainFailOpenOnDbOutage:
    """2026-07-28: ensure_news_cache_table()/recent_watchlist_articles() had
    zero exception handling -- a DB outage would raise before main() ever
    reached write_sentiment_cache(), silently killing the live tick loop's
    sentiment refresh. Both call sites are now wrapped so main() always
    reaches write_sentiment_cache() regardless of DB availability."""

    def _stub_main_deps(self, monkeypatch, written):
        monkeypatch.setattr(news_collector, "fetch_all_feeds", lambda: [])
        monkeypatch.setattr(news_collector, "upsert_articles", lambda articles: 0)
        monkeypatch.setattr(news_collector, "fetch_alpaca_news", lambda tickers: [])

        def fake_write(ticker_sentiment, path=news_collector.SENTIMENT_CACHE_PATH):
            written["called"] = True
            written["data"] = ticker_sentiment
        monkeypatch.setattr(news_collector, "write_sentiment_cache", fake_write)

    def test_ensure_table_failure_does_not_crash_main(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["news_collector.py", "AAA"])

        def raise_ensure_table():
            raise ConnectionError("simulated DB outage")
        monkeypatch.setattr(news_collector, "ensure_news_cache_table", raise_ensure_table)
        monkeypatch.setattr(news_collector, "recent_watchlist_articles", lambda tickers, hours=24: [])

        written = {}
        self._stub_main_deps(monkeypatch, written)

        news_collector.main()  # must not raise

        assert written.get("called") is True

    def test_recent_watchlist_articles_failure_does_not_crash_main(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["news_collector.py", "AAA"])
        monkeypatch.setattr(news_collector, "ensure_news_cache_table", lambda: None)

        def raise_recent(tickers, hours=24):
            raise ConnectionError("simulated DB outage")
        monkeypatch.setattr(news_collector, "recent_watchlist_articles", raise_recent)

        written = {}
        self._stub_main_deps(monkeypatch, written)

        news_collector.main()  # must not raise

        assert written.get("called") is True


class TestMainPreservesCacheOnFetchFailure:
    """2026-07-30: fetch_alpaca_news() failing (returns None) must NOT
    overwrite state/sentiment_cache.json with empty data -- that's the bug
    that left the cache silently blank for 22+ days. A failed fetch should
    skip write_sentiment_cache() entirely, leaving the last good cache in
    place."""

    def _stub_main_deps(self, monkeypatch, fetch_return):
        monkeypatch.setattr(sys, "argv", ["news_collector.py", "AAA"])
        monkeypatch.setattr(news_collector, "ensure_news_cache_table", lambda: None)
        monkeypatch.setattr(news_collector, "recent_watchlist_articles", lambda tickers, hours=24: [])
        monkeypatch.setattr(news_collector, "fetch_all_feeds", lambda: [])
        monkeypatch.setattr(news_collector, "upsert_articles", lambda articles: 0)
        monkeypatch.setattr(news_collector, "fetch_alpaca_news", lambda tickers: fetch_return)

    def test_failed_fetch_skips_cache_write(self, monkeypatch):
        written = {"called": False}

        def fake_write(ticker_sentiment, path=news_collector.SENTIMENT_CACHE_PATH):
            written["called"] = True
        monkeypatch.setattr(news_collector, "write_sentiment_cache", fake_write)

        self._stub_main_deps(monkeypatch, fetch_return=None)
        news_collector.main()  # must not raise

        assert written["called"] is False

    def test_successful_fetch_still_writes_cache(self, monkeypatch):
        written = {"called": False}

        def fake_write(ticker_sentiment, path=news_collector.SENTIMENT_CACHE_PATH):
            written["called"] = True
        monkeypatch.setattr(news_collector, "write_sentiment_cache", fake_write)

        self._stub_main_deps(monkeypatch, fetch_return=[])
        news_collector.main()  # must not raise

        assert written["called"] is True


class TestNewsCacheMigratedToLocalDb:
    """Migrated 2026-07-28 from remote Postgres (docker.klo) to local
    trader_db.py -- real sqlite3 under tmp_path via db_path=."""

    def test_ensure_table_creates_schema(self, tmp_path):
        db_path = tmp_path / "trader.db"
        news_collector.ensure_news_cache_table(db_path=db_path)
        conn = trader_db.get_conn(db_path)
        try:
            assert conn.execute("SELECT COUNT(*) AS n FROM news_cache").fetchone()["n"] == 0
        finally:
            conn.close()

    def test_upsert_articles_stores_and_returns_count(self, tmp_path):
        db_path = tmp_path / "trader.db"
        n = news_collector.upsert_articles([
            {"url": "http://a", "title": "AAA news", "source": "rss",
             "published": "2026-07-28T11:00:00Z", "tickers": ["AAA"], "sentiment_score": 0.5},
        ], db_path=db_path)
        assert n == 1
        conn = trader_db.get_conn(db_path)
        try:
            row = conn.execute("SELECT * FROM news_cache WHERE url = 'http://a'").fetchone()
            assert row["title"] == "AAA news"
            assert json.loads(row["tickers"]) == ["AAA"]
        finally:
            conn.close()

    def test_upsert_articles_duplicate_url_not_reinserted(self, tmp_path):
        db_path = tmp_path / "trader.db"
        article = {"url": "http://a", "title": "AAA news", "source": "rss",
                    "published": "2026-07-28T11:00:00Z", "tickers": ["AAA"]}
        news_collector.upsert_articles([article], db_path=db_path)
        n = news_collector.upsert_articles([article], db_path=db_path)
        assert n == 0

    def test_upsert_articles_empty_list_no_db_touch(self, tmp_path):
        db_path = tmp_path / "trader.db"
        assert news_collector.upsert_articles([], db_path=db_path) == 0
        assert not db_path.exists()

    def test_recent_watchlist_articles_filters_by_ticker(self, tmp_path):
        db_path = tmp_path / "trader.db"
        news_collector.upsert_articles([
            {"url": "http://a", "title": "AAA news", "source": "rss",
             "published": "2026-07-28T11:00:00Z", "tickers": ["AAA"]},
            {"url": "http://b", "title": "unrelated", "source": "rss",
             "published": "2026-07-28T11:00:00Z", "tickers": ["ZZZ"]},
        ], db_path=db_path)
        result = news_collector.recent_watchlist_articles(["AAA"], hours=24 * 365, db_path=db_path)
        assert len(result) == 1
        assert result[0]["title"] == "AAA news"

    def test_recent_watchlist_articles_no_tickers_returns_empty_no_db_touch(self, tmp_path):
        db_path = tmp_path / "trader.db"
        assert news_collector.recent_watchlist_articles([], db_path=db_path) == []
        assert not db_path.exists()


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
        text = "SOFI and PLTR both moved after the same catalyst headline."
        result = news_collector.extract_tickers(text, news_collector.KNOWN_TICKERS)
        assert result == ["SOFI", "PLTR"]


class TestScoreSentiment:
    """score_sentiment() added 2026-07-23: real FinBERT scoring with a
    keyword-based fallback when FinBERT is unreachable or errors."""

    def test_empty_text_returns_zero_without_calling_finbert(self, monkeypatch):
        called = []
        monkeypatch.setattr(news_collector.requests, "post", lambda *a, **k: called.append(1))
        assert news_collector.score_sentiment("") == 0.0
        assert called == []

    def test_uses_finbert_score_on_success(self, monkeypatch):
        class FakeResp:
            status_code = 200
            def json(self):
                return {"sentiment_score": 0.87, "label": "positive"}
        monkeypatch.setattr(news_collector.requests, "post", lambda *a, **k: FakeResp())
        assert news_collector.score_sentiment("great earnings beat") == 0.87

    def test_falls_back_to_keyword_on_non_200(self, monkeypatch):
        class FakeResp:
            status_code = 500
            text = "server error"
        monkeypatch.setattr(news_collector.requests, "post", lambda *a, **k: FakeResp())
        # "surge" is a known positive keyword -> nonzero fallback score.
        result = news_collector.score_sentiment("shares surge on strong demand")
        assert result == news_collector._compute_sentiment("shares surge on strong demand")

    def test_falls_back_to_keyword_on_connection_error(self, monkeypatch):
        def raise_error(*a, **k):
            raise news_collector.requests.RequestException("connection refused")
        monkeypatch.setattr(news_collector.requests, "post", raise_error)
        result = news_collector.score_sentiment("shares plunge on weak guidance")
        assert result == news_collector._compute_sentiment("shares plunge on weak guidance")

    def test_falls_back_on_malformed_response(self, monkeypatch):
        class FakeResp:
            status_code = 200
            def json(self):
                return {"unexpected_shape": True}
        monkeypatch.setattr(news_collector.requests, "post", lambda *a, **k: FakeResp())
        result = news_collector.score_sentiment("neutral filing update")
        assert result == news_collector._compute_sentiment("neutral filing update")


class TestFetchAlpacaNews:
    """fetch_alpaca_news() added 2026-07-23: ticker-scoped Alpaca News,
    tagged via Alpaca's own `symbols` field (not regex)."""

    def test_missing_credentials_returns_none(self, monkeypatch):
        """None (not []) signals a failed fetch, distinct from a legitimate
        zero-results success — see 2026-07-30 sentiment_cache.json fix."""
        monkeypatch.delenv("ALPACA_STONKS_KEY", raising=False)
        monkeypatch.delenv("ALPACA_STONKS_SECRET", raising=False)
        assert news_collector.fetch_alpaca_news(["NVDA"]) is None

    def test_empty_ticker_list_returns_empty_without_request(self, monkeypatch):
        monkeypatch.setenv("ALPACA_STONKS_KEY", "k")
        monkeypatch.setenv("ALPACA_STONKS_SECRET", "s")
        called = []
        monkeypatch.setattr(news_collector.requests, "get", lambda *a, **k: called.append(1))
        assert news_collector.fetch_alpaca_news([]) == []
        assert called == []

    def test_maps_alpaca_response_shape_with_own_ticker_tags(self, monkeypatch):
        monkeypatch.setenv("ALPACA_STONKS_KEY", "k")
        monkeypatch.setenv("ALPACA_STONKS_SECRET", "s")

        class FakeResp:
            status_code = 200
            def json(self):
                return {"news": [{
                    "headline": "Snap downgraded", "summary": "Analyst cuts target",
                    "symbols": ["snap"], "source": "benzinga", "url": "http://x/1",
                    "created_at": "2026-07-22T10:00:00Z",
                }]}
        monkeypatch.setattr(news_collector.requests, "get", lambda *a, **k: FakeResp())
        result = news_collector.fetch_alpaca_news(["SNAP"])
        assert len(result) == 1
        assert result[0]["title"] == "Snap downgraded"
        assert result[0]["tickers"] == ["SNAP"]  # uppercased
        assert result[0]["source"] == "alpaca_news"

    def test_non_200_returns_none(self, monkeypatch):
        monkeypatch.setenv("ALPACA_STONKS_KEY", "k")
        monkeypatch.setenv("ALPACA_STONKS_SECRET", "s")

        class FakeResp:
            status_code = 429
            text = "rate limited"
        monkeypatch.setattr(news_collector.requests, "get", lambda *a, **k: FakeResp())
        assert news_collector.fetch_alpaca_news(["NVDA"]) is None

    def test_request_exception_returns_none(self, monkeypatch):
        monkeypatch.setenv("ALPACA_STONKS_KEY", "k")
        monkeypatch.setenv("ALPACA_STONKS_SECRET", "s")

        def raise_error(*a, **k):
            raise news_collector.requests.RequestException("connection reset")
        monkeypatch.setattr(news_collector.requests, "get", raise_error)
        assert news_collector.fetch_alpaca_news(["NVDA"]) is None


class TestBuildTickerSentiment:
    """build_ticker_sentiment() added 2026-07-23: aggregates already-scored
    articles into a per-ticker summary for the sentiment cache."""

    def _article(self, tickers, score, title="headline", source="alpaca_news", published="2026-07-22T10:00:00+00:00"):
        return {"tickers": tickers, "sentiment_score": score, "title": title,
                "source": source, "published": published}

    def test_averages_multiple_articles_for_same_ticker(self):
        articles = [self._article(["NVDA"], 0.5), self._article(["NVDA"], -0.1)]
        result = news_collector.build_ticker_sentiment(articles, ["NVDA"])
        assert result["NVDA"]["avg_sentiment"] == 0.2
        assert result["NVDA"]["article_count"] == 2

    def test_ignores_tickers_not_in_wanted_list(self):
        articles = [self._article(["ZZZZ"], 0.5)]
        result = news_collector.build_ticker_sentiment(articles, ["NVDA"])
        assert result == {}

    def test_case_insensitive_ticker_matching(self):
        articles = [self._article(["nvda"], 0.5)]
        result = news_collector.build_ticker_sentiment(articles, ["NVDA"])
        assert "NVDA" in result

    def test_latest_headline_is_most_recently_published(self):
        articles = [
            self._article(["NVDA"], 0.1, title="older", published="2026-07-20T10:00:00+00:00"),
            self._article(["NVDA"], 0.2, title="newer", published="2026-07-22T10:00:00+00:00"),
        ]
        result = news_collector.build_ticker_sentiment(articles, ["NVDA"])
        assert result["NVDA"]["latest_headline"] == "newer"

    def test_article_can_match_multiple_tickers(self):
        articles = [self._article(["NVDA", "AMD"], 0.4)]
        result = news_collector.build_ticker_sentiment(articles, ["NVDA", "AMD"])
        assert result["NVDA"]["article_count"] == 1
        assert result["AMD"]["article_count"] == 1


class TestWriteSentimentCache:
    def test_writes_valid_json_with_generated_at_and_tickers(self, tmp_path):
        cache_path = tmp_path / "state" / "sentiment_cache.json"
        news_collector.write_sentiment_cache({"NVDA": {"avg_sentiment": 0.1}}, path=cache_path)
        assert cache_path.exists()
        data = json.loads(cache_path.read_text())
        assert "generated_at" in data
        assert data["tickers"]["NVDA"]["avg_sentiment"] == 0.1

    def test_creates_parent_directory_if_missing(self, tmp_path):
        cache_path = tmp_path / "nested" / "dir" / "sentiment_cache.json"
        news_collector.write_sentiment_cache({}, path=cache_path)
        assert cache_path.exists()

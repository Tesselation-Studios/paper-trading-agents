#!/usr/bin/env python3
"""
News Collector — RSS feed aggregation with caching, ticker extraction,
and keyword-based sentiment scoring.

Adapted from paper-trading-rebuild/src/news_collector.py (unmerged branch
trader/news-collector) for use as a one-shot off-hours script instead of
the always-on daemon-thread version — Stonks runs this directly via its
own exec tool, same pattern as scripts/executor.py.

Fetches from free RSS feeds (no API keys), deduplicates by URL, stores
results in the shared Postgres `public.news_cache` table (additive-only,
idempotent CREATE IF NOT EXISTS — doesn't touch any other table).

Usage:
    python3 scripts/news_collector.py
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import requests

log = logging.getLogger("news_collector")
logging.basicConfig(level=logging.INFO, format="%(message)s")

# ── RSS Feed Sources ──────────────────────────────────────────────────────────
# Free, no API key required. Sourced from major financial publishers.

RSS_FEEDS: Dict[str, str] = {
    "marketwatch": "https://feeds.marketwatch.com/marketwatch/topstories/",
    "marketwatch_rss": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "yahoo": "https://finance.yahoo.com/news/rssindex",
    "bloomberg": "https://feeds.bloomberg.com/markets/news.rss",
    "cnbc": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "seekingalpha": "https://seekingalpha.com/feed.xml",
}

# ── VADER-compatible keyword sentiment (mirrors data_bus.py _simple_sentiment) ─

_SENTIMENT_POSITIVE: Set[str] = {
    "bullish", "surge", "surged", "soar", "soared", "rally", "rallied",
    "upgrade", "upgraded", "outperform", "beat", "beats", "exceed",
    "exceeded", "strong", "growth", "profit", "profits", "record",
    "breakout", "boom", "innovation", "leader", "leading", "optimistic",
    "positive", "momentum", "gains", "gain", "rising", "rise",
    "rebound", "recovery", "opportunity", "opportunities", "dividend",
    "dividends", "buyback", "expansion", "expand", "approved",
    "breakthrough", "partnership", "launch", "success", "successful",
    "confidence", "confident", "outlook", "upside", "potential",
    "bargain", "undervalued", "overweight", "overweighted", "accumulate",
    "adding", "boost", "boosts", "skyrocket", "skyrocketed", "jump",
    "jumped", "pop", "spike", "spiked", "green", "profitability",
    "efficient", "efficiency", "raised", "raising", "target", "increase",
    "increased", "increasing",
}

_SENTIMENT_NEGATIVE: Set[str] = {
    "bearish", "plunge", "plunged", "crash", "crashed", "slump", "slumped",
    "downgrade", "downgraded", "underperform", "miss", "misses", "missed",
    "decline", "declined", "weak", "weakness", "loss", "losses", "debt",
    "liability", "risk", "risky", "volatile", "volatility", "uncertainty",
    "negative", "downturn", "recession", "inflation", "layoff", "layoffs",
    "cut", "cuts", "cutting", "sell", "selling", "sold", "dump",
    "dumped", "short", "shorted", "bear", "collapse", "collapsed",
    "bankrupt", "bankruptcy", "fraud", "investigation", "fine", "fined",
    "lawsuit", "penalty", "sanction", "deficit", "declining", "slowdown",
    "struggle", "struggling", "red", "warning", "warn", "warned",
    "underweight", "reduce", "pressure", "concern", "concerning",
    "worst", "fail", "failed", "failure", "drop", "dropped", "fall",
    "fallen", "fell", "lower", "lowered", "decrease", "decreased",
    "tightening",
}

_SENTIMENT_INTENSIFIERS: Set[str] = {
    "very", "extremely", "highly", "strongly", "significantly",
    "substantially", "massively", "dramatically", "sharply", "deeply",
}

# ── Known Tickers ─────────────────────────────────────────────────────────────
# Curated set of ~1000 heavily traded symbols (SP500 + NASDAQ100 + common ETFs)
# — already covers Stonks's small-cap holdings (SOFI, PLTR, GME, AMC, COIN,
# HOOD, CHWY, etc), kept broad rather than narrowed to the current watchlist
# since a wider net helps future discovery too.

KNOWN_TICKERS: Set[str] = {
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "NVDA", "META", "BRK.B", "BRK.A",
    "TSLA", "UNH", "LLY", "JPM", "V", "XOM", "AVGO", "PG", "MA", "HD", "CVX",
    "MRK", "ABBV", "PEP", "KO", "COST", "ADBE", "WMT", "CRM", "BAC", "NFLX",
    "DIS", "AMD", "PYPL", "CMCSA", "TMO", "INTC", "VZ", "QCOM", "TXN", "NKE",
    "BA", "ABT", "NEE", "MS", "HON", "PM", "IBM", "DHR", "T", "RTX",
    "SPGI", "LOW", "CAT", "UNP", "AMGN", "GS", "COP", "AXP", "INTU", "BKNG",
    "TJT", "BLK", "CB", "SYK", "PLD", "SCHW", "SHEL", "C", "TMUS", "FI",
    "UPS", "DE", "ADP", "GILD", "PFE", "MMC", "BMY", "LMT", "TTE", "CMG",
    "SO", "DUK", "CI", "MDT", "ETN", "UBER", "MU", "MO", "NOC", "PNC",
    "EOG", "USO", "SLB", "FCX", "AON", "APD", "ITW", "MPC", "EMR", "ICE",
    "ZTS", "BDX", "CL", "MDLZ", "GD", "NSC", "TGT", "EQIX", "WELL", "GM",
    "OXY", "KMI", "PSX", "WMB", "OKE", "MMM", "HCA", "FDX", "SHW", "SPG",
    "DLR", "CSCO", "HUM", "CCI", "VRTX", "PLTR", "MCO", "TRV", "FISV", "AIG",
    "ALL", "MET", "PRU", "AFL", "HIG", "BRO", "AIZ", "LNC", "GL", "TW",
    "ERIE", "MKL", "CNA", "ACGL", "WRB", "CB", "WFC", "USB", "TFC", "PNFP",
    "FITB", "HBAN", "CFG", "RF", "KEY", "MTB", "STT", "NTRS",
    "WBD", "PARA", "FOXA", "FOX", "OMC", "IPG", "EA", "TTWO", "RBLX",
    "MANH", "ANSS", "CDNS", "SNPS", "PANW", "FTNT", "CHTR",
    "DASH", "ZM", "WDAY", "TEAM", "CRWD", "DDOG", "MDB", "MRNA",
    "BIIB", "SAGE", "SRPT", "ALKS", "ILMN", "DXCM", "PODD", "ISRG",
    "MSCI", "KKR", "COIN", "HOOD", "SQ", "SHOP", "AFRM", "MELI",
    "ENPH", "SEDG", "FSLR", "GE", "ROK", "AME",
    "PH", "JCI", "CARR", "TT", "OTIS", "IR", "TRMB", "GNRC",
    "ABNB", "EXPE", "HLT", "MAR", "CCL", "RCL", "NCLH",
    "LVS", "MGM", "WYNN", "DAL", "UAL", "AAL", "LUV", "JBLU",
    "SAVE", "XPEV", "NIO", "LI", "LCID", "RIVN", "F",
    "STLA", "VOW3.DE", "BMW.DE", "MBG.DE", "RACE",
    "SPY", "IVV", "VOO", "QQQ", "VTI", "IWM", "DIA", "TLT", "IEF",
    "AGG", "BND", "GLD", "SLV", "XLF", "XLE", "XLK", "XLV",
    "XLI", "XLP", "XLU", "XLY", "XLRE", "XLC", "XLB", "VIG", "VYM",
    "SCHD", "SCHX", "VT", "VXUS", "BNDX", "EMB", "HYG", "LQD",
    "ARKK", "ARKG", "ARKF", "ARKQ", "ARKW", "ICLN", "TAN",
    "SOXX", "SMH", "XSD", "IBB", "XBI", "LABU", "KRE", "KBE",
    "EWJ", "EWZ", "EEM", "VWO", "FXI", "KWEB", "INDA", "EPI",
    "URA", "UUP", "FXE", "FXB", "FXY",
    "ADI", "ADSK", "AEP", "ALGN", "AMAT", "ARM", "ASML", "AZN", "BKR",
    "CCEP", "CPRT", "CSGP", "CSX", "CTAS", "DLTR", "EBAY", "ENPH", "EXC",
    "FAST", "GEHC", "GFS", "IDXX", "JD", "KDP", "KHC", "KLAC", "LRCX",
    "LULU", "MCHP", "MNST", "MRVL", "NTES", "NXPI", "ODFL", "ORLY",
    "PAYX", "PCAR", "REGN", "ROST", "SBUX", "SGEN", "SIRI", "SPLK",
    "SWKS", "TCOM", "VRSK", "WBA", "XEL", "ZS",
    "SOFI", "PLTR", "RKLB", "ASTS", "IONQ", "RDDT", "GME", "AMC",
    "CLSK", "MARA", "RIOT", "MSTR", "HIMS", "CROX", "DKNG",
    "PENN", "SNAP", "PINS", "MTCH", "BMBL", "FVRR",
    "UPST", "CHWY", "WOLF", "ON", "STM", "UMC", "TSM",
    "FUBO", "MVST", "OPEN",
}

_DSN: Optional[str] = None


def _get_dsn() -> str:
    global _DSN
    if _DSN is None:
        host = os.getenv("PGHOST", "docker.klo")
        port = os.getenv("PGPORT", "5433")
        dbname = os.getenv("PGDATABASE", "trading")
        user = os.getenv("PGUSER", "trader")
        pw = os.getenv("PGPASSWORD", "")
        _DSN = f"host={host} port={port} dbname={dbname} user={user}"
        if pw:
            _DSN += f" password={pw}"
    return _DSN


def _compute_sentiment(text: str) -> float:
    if not text:
        return 0.0
    words = re.findall(r"[a-zA-Z]+", text.lower())
    if not words:
        return 0.0
    score = 0.0
    n_matched = 0
    for i, w in enumerate(words):
        multiplier = 1.0
        if i > 0 and words[i - 1] in _SENTIMENT_INTENSIFIERS:
            multiplier = 1.5
        if i > 0 and words[i - 1] in {"not", "no", "never", "neither", "nor"}:
            multiplier = -1.0
        if w in _SENTIMENT_POSITIVE:
            score += 0.3 * multiplier
            n_matched += 1
        elif w in _SENTIMENT_NEGATIVE:
            score -= 0.3 * multiplier
            n_matched += 1
    if n_matched == 0:
        return 0.0
    avg = score / n_matched
    return max(-1.0, min(1.0, avg))


def fetch_rss_feed(url: str, timeout: int = 15) -> List[Dict[str, Any]]:
    try:
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) PaperTrading/1.0",
        })
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("RSS fetch failed for %s: %s", url[:60], e)
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as e:
        log.warning("RSS parse failed for %s: %s", url[:60], e)
        return []

    articles: List[Dict[str, Any]] = []

    for item in root.iter("item"):
        title = _get_element_text(item, "title") or ""
        link = _get_element_text(item, "link") or ""
        summary = _get_element_text(item, "description") or ""
        pub_date_str = _get_element_text(item, "pubDate") or ""
        if not title and not link:
            continue
        published = _parse_rss_date(pub_date_str)
        articles.append({
            "title": title.strip(), "url": link.strip(),
            "summary": re.sub(r"<[^>]+>", "", summary).strip() if summary else "",
            "published": published,
        })

    for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
        title_elem = entry.find("{http://www.w3.org/2005/Atom}title")
        link_elem = entry.find("{http://www.w3.org/2005/Atom}link")
        summary_elem = entry.find("{http://www.w3.org/2005/Atom}summary")
        published_elem = entry.find("{http://www.w3.org/2005/Atom}published")
        updated_elem = entry.find("{http://www.w3.org/2005/Atom}updated")

        title = title_elem.text.strip() if title_elem is not None and title_elem.text else ""
        link = link_elem.get("href", "") if link_elem is not None else ""
        summary = summary_elem.text.strip() if summary_elem is not None and summary_elem.text else ""
        pub_str = ""
        if published_elem is not None and published_elem.text:
            pub_str = published_elem.text
        elif updated_elem is not None and updated_elem.text:
            pub_str = updated_elem.text
        if not title and not link:
            continue
        published = _parse_atom_date(pub_str)
        articles.append({
            "title": title.strip(), "url": link.strip(),
            "summary": re.sub(r"<[^>]+>", "", summary).strip() if summary else "",
            "published": published,
        })

    return articles


def _get_element_text(parent: ET.Element, tag: str) -> Optional[str]:
    elem = parent.find(tag)
    if elem is not None and elem.text:
        return elem.text.strip()
    return None


_RSS_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_rss_date(date_str: str) -> str:
    if not date_str:
        return datetime.now(timezone.utc).isoformat()
    cleaned = date_str.strip()
    if "," in cleaned:
        cleaned = cleaned.split(",", 1)[1].strip()
    parts = cleaned.split()
    if len(parts) < 4:
        return datetime.now(timezone.utc).isoformat()
    try:
        day = int(parts[0])
        month = _RSS_MONTHS.get(parts[1].lower()[:3], 1)
        year = int(parts[2])
        time_parts = parts[3].split(":")
        hour = int(time_parts[0]) if len(time_parts) > 0 else 0
        minute = int(time_parts[1]) if len(time_parts) > 1 else 0
        second = int(time_parts[2]) if len(time_parts) > 2 else 0
        dt = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, IndexError):
        return datetime.now(timezone.utc).isoformat()


def _parse_atom_date(date_str: str) -> str:
    if not date_str:
        return datetime.now(timezone.utc).isoformat()
    try:
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError):
        pass
    try:
        normalized = date_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        return dt.isoformat()
    except (ValueError, TypeError):
        return datetime.now(timezone.utc).isoformat()


def extract_tickers(text: str, known_tickers: Set[str]) -> List[str]:
    if not text:
        return []
    candidates = re.findall(r"\b[A-Z]{1,5}(?:\.[A-Z]{1,3})?\b", text.upper())
    seen: Set[str] = set()
    result: List[str] = []
    for c in candidates:
        if c in known_tickers and c not in seen:
            seen.add(c)
            result.append(c)
    return result


def _deduplicate(articles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen_urls: Set[str] = set()
    deduped: List[Dict[str, Any]] = []
    for a in articles:
        u = a.get("url", "")
        if u and u not in seen_urls:
            seen_urls.add(u)
            deduped.append(a)
    return deduped


def fetch_all_feeds(timeout: int = 15) -> List[Dict[str, Any]]:
    all_articles: List[Dict[str, Any]] = []
    for source_name, url in RSS_FEEDS.items():
        articles = fetch_rss_feed(url, timeout=timeout)
        log.info("Fetched %d articles from %s", len(articles), source_name)
        for article in articles:
            article["source"] = source_name
            combined = f"{article.get('title', '')} {article.get('summary', '')}"
            article["sentiment_score"] = _compute_sentiment(combined)
            article["tickers"] = extract_tickers(combined, KNOWN_TICKERS)
        all_articles.extend(articles)
    deduped = _deduplicate(all_articles)
    log.info("Total articles: %d (deduplicated from %d)", len(deduped), len(all_articles))
    return deduped


def ensure_news_cache_table() -> None:
    """Additive-only, idempotent — same public.news_cache table the
    unmerged trader/news-collector branch defines, so this stays
    compatible if that branch ever merges."""
    import psycopg2
    sql = """
    CREATE TABLE IF NOT EXISTS public.news_cache (
        id SERIAL PRIMARY KEY,
        url TEXT UNIQUE NOT NULL,
        title TEXT NOT NULL,
        summary TEXT,
        source TEXT NOT NULL,
        published_at TIMESTAMPTZ NOT NULL,
        collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        tickers TEXT[],
        sentiment_score FLOAT DEFAULT 0.0,
        full_text TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_news_cache_published ON public.news_cache(published_at DESC);
    CREATE INDEX IF NOT EXISTS idx_news_cache_tickers ON public.news_cache USING GIN(tickers);
    CREATE INDEX IF NOT EXISTS idx_news_cache_source ON public.news_cache(source);
    """
    conn = psycopg2.connect(_get_dsn())
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    finally:
        conn.close()


def upsert_articles(articles: List[Dict[str, Any]]) -> int:
    import psycopg2
    import psycopg2.extras

    if not articles:
        return 0

    conn = psycopg2.connect(_get_dsn())
    try:
        with conn.cursor() as cur:
            rows = []
            for a in articles:
                published = a.get("published", "")
                if published:
                    try:
                        dt = datetime.fromisoformat(published)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        published = dt.isoformat()
                    except (ValueError, TypeError):
                        published = datetime.now(timezone.utc).isoformat()
                else:
                    published = datetime.now(timezone.utc).isoformat()
                tickers = a.get("tickers", [])
                rows.append((
                    a.get("url", ""), a.get("title", ""), a.get("summary", None),
                    a.get("source", ""), published,
                    tickers if isinstance(tickers, list) else [],
                    float(a.get("sentiment_score", 0.0)), a.get("full_text", None),
                ))
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO public.news_cache
                   (url, title, summary, source, published_at, tickers, sentiment_score, full_text)
                   VALUES %s
                   ON CONFLICT (url) DO NOTHING""",
                rows, template="""(%s, %s, %s, %s, %s::timestamptz, %s::text[], %s, %s)""",
            )
            n = cur.rowcount
        conn.commit()
        return n
    except Exception as e:
        log.warning("Failed to upsert articles: %s", e)
        conn.rollback()
        return 0
    finally:
        conn.close()


def recent_watchlist_articles(watchlist_tickers, hours=24):
    """Query the accumulated cache (not just this run's fresh fetch) for
    anything touching the watchlist in the last N hours — a single
    collection pass often won't catch a relevant article for every ticker,
    but the accumulated cache usually will."""
    import psycopg2

    if not watchlist_tickers:
        return []
    watchlist = [t.upper() for t in watchlist_tickers]
    conn = psycopg2.connect(_get_dsn())
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT title, tickers, sentiment_score, source, published_at
               FROM public.news_cache
               WHERE tickers && %s AND published_at > NOW() - (%s || ' hours')::interval
               ORDER BY published_at DESC LIMIT 50""",
            (watchlist, hours),
        )
        rows = cur.fetchall()
        return [
            {"title": r[0], "ticker_hits": r[1], "sentiment": round(float(r[2]), 2),
             "source": r[3], "published_at": r[4].isoformat()}
            for r in rows
        ]
    finally:
        conn.close()


def main():
    watchlist_tickers = sys.argv[1:] if len(sys.argv) > 1 else []

    ensure_news_cache_table()
    articles = fetch_all_feeds()
    new_count = upsert_articles(articles)

    relevant = recent_watchlist_articles(watchlist_tickers) if watchlist_tickers else []
    avg_sentiment_by_ticker = {}
    for a in relevant:
        for t in a["ticker_hits"]:
            if watchlist_tickers and t not in [w.upper() for w in watchlist_tickers]:
                continue
            avg_sentiment_by_ticker.setdefault(t, []).append(a["sentiment"])
    avg_sentiment_by_ticker = {
        t: round(sum(v) / len(v), 3) for t, v in avg_sentiment_by_ticker.items()
    }

    summary = {
        "fetched_this_run": len(articles),
        "new_to_cache": new_count,
        "watchlist_articles_last_24h": len(relevant),
        "avg_sentiment_by_ticker_last_24h": avg_sentiment_by_ticker,
        "articles": relevant[:20],
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

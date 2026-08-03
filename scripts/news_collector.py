#!/usr/bin/env python3
"""
News Collector — RSS + Alpaca News aggregation, FinBERT sentiment scoring
(keyword-based fallback), ticker extraction, and a local per-ticker
sentiment cache for the live tick loop.

Adapted from paper-trading-rebuild/src/news_collector.py (unmerged branch
trader/news-collector) for use as a one-shot off-hours script instead of
the always-on daemon-thread version — Stonks runs this directly via its
own exec tool, same pattern as scripts/executor.py.

2026-07-23: added direct Alpaca News (ticker-scoped, more reliable for
small-caps than generic RSS firehoses) and real FinBERT scoring (keyword
scoring kept only as the fail-open fallback). Root cause of the "sentiment
blind" gap wasn't FinBERT/Praesentire being down (confirmed both healthy)
— it was that nothing ever populated a per-ticker cache the live tick loop
could read. `write_sentiment_cache()` fixes that: a local
`state/sentiment_cache.json`, refreshed on its own cadence (see
stonks-sentiment-refresh cron), so the tick loop gets a fast local read
instead of a live network round-trip mid-tick.

2026-08-03: FinBERT scoring moved off the standalone HTTP service
(legend-of-macs.local:5004, separate repo/deploy) onto the gpu-compute
worker's `sentiment` job type (gRPC, same worker already used for regime
retraining) — all ML work now routes through one service. See
score_sentiment_batch()/score_sentiment() below; keyword fallback
unchanged.

Fetches from free RSS feeds (no API keys) + Alpaca News (ALPACA_STONKS_KEY/
SECRET), deduplicates by URL, stores results in the local trader_db.py
news_cache table (additive-only, migrated 2026-07-28 from remote Postgres
on docker.klo).

Usage:
    python3 scripts/news_collector.py [TICKER ...]

With no TICKER args, defaults to replay_check.load_live_universe() (open
positions + active watchlist candidates) — same convention already used by
replay_check.py/universe_scan.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import trader_db  # noqa: E402

log = logging.getLogger("news_collector")
logging.basicConfig(level=logging.INFO, format="%(message)s")

REPO_ROOT = Path(__file__).resolve().parent.parent
SENTIMENT_CACHE_PATH = REPO_ROOT / "state" / "sentiment_cache.json"

# gpu-compute worker (gRPC, sentiment job type) -- see score_sentiment_batch().
GPU_COMPUTE_ROOT = os.environ.get("GPU_COMPUTE_ROOT", str(Path.home() / "projects" / "gpu-compute"))
if GPU_COMPUTE_ROOT not in sys.path:
    sys.path.insert(0, GPU_COMPUTE_ROOT)
SENTIMENT_JOB_TIMEOUT = int(os.environ.get("SENTIMENT_JOB_TIMEOUT", "30"))

ALPACA_NEWS_URL = "https://data.alpaca.markets/v1beta1/news"

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


def _score_sentiment_batch_via_worker(texts: List[str]) -> Optional[List[float]]:
    """One gRPC round-trip through the gpu-compute worker's `sentiment` job
    type for the whole batch. Returns None (not a list of zeros) on any
    failure -- worker unreachable, job failed/timed out, unexpected
    response shape -- so the caller falls back to the keyword scorer
    instead of silently returning wrong scores."""
    try:
        from generated import gpu_compute_pb2 as pb
        from orchestrator.gpu_client import WorkerPool
    except ImportError as e:
        log.warning("gpu_client unavailable (%s), falling back to keyword sentiment", e)
        return None

    async def _run() -> Optional[List[float]]:
        pool = WorkerPool.from_env()
        try:
            job_id = await pool.submit_sentiment(texts=texts)
            if job_id is None:
                return None
            update = await pool.wait_for_job(job_id, timeout=SENTIMENT_JOB_TIMEOUT)
            if update is None or update.phase != pb.JobPhase.COMPLETED:
                return None
            result = json.loads(update.result_json)
            return [r["polarity"] * r["score"] for r in result["results"]]
        finally:
            await pool.close()

    try:
        return asyncio.run(_run())
    except Exception as e:
        log.warning("Sentiment worker call failed (%s), falling back to keyword sentiment", e)
        return None


def score_sentiment_batch(texts: List[str]) -> List[float]:
    """Score a batch of texts in a single gRPC round-trip to the gpu-compute
    worker's `sentiment` job type (real FinBERT, signed -1..1). Falls back
    to the keyword scorer (_compute_sentiment) per-text if the worker is
    unreachable, the job fails, or times out -- fail-open, same "never let
    a tick stall" philosophy as the rest of this pipeline. Returns a list
    the same length and order as `texts`. Prefer this over score_sentiment()
    for anything scoring more than one text -- each score_sentiment() call
    is its own round-trip.
    """
    if not texts:
        return []
    scores = _score_sentiment_batch_via_worker(texts)
    if scores is not None and len(scores) == len(texts):
        return scores
    return [_compute_sentiment(t) for t in texts]


def score_sentiment(text: str, ticker: str = "", timeout: int = 8) -> float:
    """Real FinBERT sentiment (signed -1..1) via the gpu-compute worker's
    `sentiment` job type (consolidated 2026-08-03 off a standalone HTTP
    FinBERT service), falling back to the keyword scorer if the worker is
    unreachable or errors. Thin single-text wrapper over
    score_sentiment_batch() for callers that only have one text at a
    time -- prefer calling that directly when scoring several texts, to
    avoid a separate gRPC round-trip per call. `ticker`/`timeout` are
    accepted for call-site compatibility but unused (SENTIMENT_JOB_TIMEOUT
    governs the round-trip; the worker doesn't take a per-text ticker).
    """
    if not text:
        return 0.0
    return score_sentiment_batch([text])[0]


def fetch_alpaca_news(tickers: List[str], limit: int = 20, timeout: int = 15) -> Optional[List[Dict[str, Any]]]:
    """Alpaca's own News API, scoped to specific tickers via `symbols=` —
    far more reliable for small/mid-caps than generic RSS firehoses, which
    mostly cover megacaps/macro. Returns the same article shape as
    fetch_rss_feed() (title/url/summary/published/source) plus `tickers`
    set directly from Alpaca's own per-article symbol tags (no regex
    extraction needed — Alpaca already tells us which tickers an article
    is about).

    Returns `None` (not `[]`) when the fetch itself failed — missing
    credentials, a non-200 response, or a network error — so callers can
    tell "we asked and got nothing" apart from "the request never
    succeeded." Collapsing both to `[]` is what let sentiment_cache.json
    go silently empty for 22+ days (2026-07-30): every failed run still
    overwrote the last good cache with nothing. An empty `tickers` list is
    not a failure (nothing to ask about), so it still returns `[]`.
    """
    key = os.environ.get("ALPACA_STONKS_KEY")
    secret = os.environ.get("ALPACA_STONKS_SECRET")
    if not tickers:
        return []
    if not key or not secret:
        log.warning("Alpaca News fetch skipped: ALPACA_STONKS_KEY/SECRET not set in environment")
        return None

    try:
        resp = requests.get(
            ALPACA_NEWS_URL,
            headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
            params={"symbols": ",".join(tickers), "limit": limit, "sort": "desc"},
            timeout=timeout,
        )
        if resp.status_code != 200:
            log.warning("Alpaca News returned %s: %s", resp.status_code, resp.text[:200])
            return None
        items = resp.json().get("news", [])
    except requests.RequestException as e:
        log.warning("Alpaca News fetch failed: %s", e)
        return None

    articles = []
    for item in items:
        summary = item.get("summary", "") or ""
        articles.append({
            "title": item.get("headline", ""),
            "url": item.get("url", ""),
            "summary": summary,
            "published": _parse_atom_date(item.get("created_at", "")),
            "source": "alpaca_news",
            "tickers": [t.upper() for t in item.get("symbols", [])],
        })
    return articles


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
    """Extract known tickers from free text.

    Excludes matches immediately adjacent to a letter/period/hyphen on
    either side (lookaround, not just \\b) so "F" doesn't false-match out
    of "F-Secure" or "e.l.f." (period splits into E/L/F, hyphen splits
    F-Secure into F/SECURE — plain \\b treats both as real word edges).
    Legitimate mentions like "Ford (F)" or dotted tickers like "BRK.A"
    still match since parens/spaces aren't in the excluded set.
    """
    if not text:
        return []
    candidates = re.findall(
        r"(?<![A-Za-z.-])[A-Z]{1,5}(?:\.[A-Z]{1,3})?(?![A-Za-z.-])", text.upper()
    )
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
        all_articles.extend(articles)

    # One batched sentiment round-trip for every article across every feed,
    # not one gRPC call per article.
    combined_texts = [f"{a.get('title', '')} {a.get('summary', '')}" for a in all_articles]
    scores = score_sentiment_batch(combined_texts)
    for article, combined, score in zip(all_articles, combined_texts, scores):
        article["sentiment_score"] = score
        article["tickers"] = extract_tickers(combined, KNOWN_TICKERS)

    deduped = _deduplicate(all_articles)
    log.info("Total articles: %d (deduplicated from %d)", len(deduped), len(all_articles))
    return deduped


def ensure_news_cache_table(db_path: Path = None) -> None:
    """Schema is created automatically by trader_db.get_conn() -- this just
    exercises that path so main()'s explicit call keeps working, kept for
    structural parity with the pre-migration (Postgres) flow."""
    conn = trader_db.get_conn(db_path)
    conn.close()


def upsert_articles(articles: List[Dict[str, Any]], db_path: Path = None) -> int:
    if not articles:
        return 0

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
        rows.append({
            "url": a.get("url", ""), "title": a.get("title", ""), "summary": a.get("summary"),
            "source": a.get("source", ""), "published_at": published,
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "tickers": json.dumps(tickers if isinstance(tickers, list) else []),
            "sentiment_score": float(a.get("sentiment_score", 0.0)), "full_text": a.get("full_text"),
        })

    try:
        conn = trader_db.get_conn(db_path)
    except Exception as e:
        log.warning("Failed to open news cache DB: %s", e)
        return 0
    try:
        return trader_db.upsert_news_articles(conn, rows)
    except Exception as e:
        log.warning("Failed to upsert articles: %s", e)
        return 0
    finally:
        conn.close()


def recent_watchlist_articles(watchlist_tickers, hours=24, db_path: Path = None):
    """Query the accumulated cache (not just this run's fresh fetch) for
    anything touching the watchlist in the last N hours — a single
    collection pass often won't catch a relevant article for every ticker,
    but the accumulated cache usually will."""
    if not watchlist_tickers:
        return []
    conn = trader_db.get_conn(db_path)
    try:
        return trader_db.recent_watchlist_articles(conn, watchlist_tickers, hours=hours)
    finally:
        conn.close()


def build_ticker_sentiment(articles: List[Dict[str, Any]], tickers: List[str]) -> Dict[str, Dict[str, Any]]:
    """Aggregate a list of already-scored articles (each with `tickers` and
    `sentiment_score`) into a per-ticker summary: average sentiment,
    article count, and the most recent headline for quick human context.
    Only includes tickers actually present in `tickers` (case-insensitive).
    """
    wanted = {t.upper() for t in tickers}
    by_ticker: Dict[str, List[Dict[str, Any]]] = {}
    for a in articles:
        for t in a.get("tickers", []):
            t = t.upper()
            if t in wanted:
                by_ticker.setdefault(t, []).append(a)

    result = {}
    for t, matched in by_ticker.items():
        scores = [m["sentiment_score"] for m in matched]
        matched_sorted = sorted(matched, key=lambda m: m.get("published", ""), reverse=True)
        result[t] = {
            "avg_sentiment": round(sum(scores) / len(scores), 3),
            "article_count": len(matched),
            "latest_headline": matched_sorted[0].get("title", ""),
            "latest_source": matched_sorted[0].get("source", ""),
        }
    return result


def write_sentiment_cache(ticker_sentiment: Dict[str, Dict[str, Any]],
                           path: Path = SENTIMENT_CACHE_PATH) -> None:
    """Local per-ticker sentiment cache for the live tick loop — a fast
    file read instead of a live Alpaca News + FinBERT round-trip mid-tick.
    Refreshed on its own cadence by the stonks-sentiment-refresh cron, not
    by the tick loop itself.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tickers": ticker_sentiment,
    }
    path.write_text(json.dumps(payload, indent=2))


def main():
    args_tickers = sys.argv[1:]
    if args_tickers:
        watchlist_tickers = args_tickers
    else:
        from replay_check import load_live_universe
        watchlist_tickers = load_live_universe()

    try:
        ensure_news_cache_table()
    except Exception as e:
        log.warning("news_cache table unavailable, skipping accumulated-cache writes this run: %s", e)

    articles = fetch_all_feeds()
    new_count = upsert_articles(articles)

    alpaca_articles = fetch_alpaca_news(watchlist_tickers)
    fetch_failed = alpaca_articles is None
    if fetch_failed:
        alpaca_articles = []
    alpaca_texts = [f"{a.get('title', '')} {a.get('summary', '')}" for a in alpaca_articles]
    alpaca_scores = score_sentiment_batch(alpaca_texts)
    for a, score in zip(alpaca_articles, alpaca_scores):
        a["sentiment_score"] = score

    try:
        relevant = recent_watchlist_articles(watchlist_tickers) if watchlist_tickers else []
    except Exception as e:
        log.warning("recent_watchlist_articles failed, continuing without accumulated-cache articles: %s", e)
        relevant = []
    avg_sentiment_by_ticker = {}
    for a in relevant:
        for t in a["ticker_hits"]:
            if watchlist_tickers and t not in [w.upper() for w in watchlist_tickers]:
                continue
            avg_sentiment_by_ticker.setdefault(t, []).append(a["sentiment"])
    avg_sentiment_by_ticker = {
        t: round(sum(v) / len(v), 3) for t, v in avg_sentiment_by_ticker.items()
    }

    # Cache-file sentiment uses ONLY Alpaca News articles, not RSS. Alpaca
    # tags each article's tickers itself (first-party, reliable); RSS
    # articles are tagged by regex over KNOWN_TICKERS, which false-matches
    # common-English-word tickers (e.g. "OPEN" — Opendoor, one of Stan's
    # actual positions — matched an RSS headline that just used the word
    # "open" in an unrelated sentence, scoring -0.723 off zero relevance).
    # RSS still feeds the broader public.news_cache archive above, just not
    # this cache, which the tick loop treats as ground truth per ticker.
    ticker_sentiment = build_ticker_sentiment(alpaca_articles, watchlist_tickers)
    if fetch_failed:
        log.warning(
            "Alpaca News fetch failed this run — leaving state/sentiment_cache.json "
            "as-is instead of overwriting it with empty data"
        )
    else:
        write_sentiment_cache(ticker_sentiment)

    summary = {
        "fetched_this_run": len(articles),
        "new_to_cache": new_count,
        "alpaca_news_fetch_failed": fetch_failed,
        "alpaca_news_fetched": len(alpaca_articles),
        "watchlist_articles_last_24h": len(relevant),
        "avg_sentiment_by_ticker_last_24h": avg_sentiment_by_ticker,
        "sentiment_cache_written_for": [] if fetch_failed else sorted(ticker_sentiment.keys()),
        "sentiment_cache_write_skipped": fetch_failed,
        "articles": relevant[:20],
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()

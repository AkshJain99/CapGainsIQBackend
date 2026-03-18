"""
core/nse_search.py
─────────────────────────────────────────────────────────────────────────────
NSE stock company name → Yahoo Finance ticker resolver.

Data source: NSE official equity list (free, no auth required)
  https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv

CSV format:
  SYMBOL, NAME OF COMPANY, SERIES, DATE OF LISTING, ...

Strategy:
  1. Fetch EQUITY_L.csv from NSE archives on startup, cache in memory
  2. Fuzzy-match a company name (e.g. "ADANI ENTERPRISES LIMITED")
     against the full list to find the NSE symbol (e.g. "ADANIENT")
  3. Append ".NS" for Yahoo Finance ticker (e.g. "ADANIENT.NS")

This solves the Zerodha import problem where:
  Zerodha stores: "ADANI ENTERPRISES LIMITED"
  Yahoo needs:    "ADANIENT.NS"
─────────────────────────────────────────────────────────────────────────────
"""

import re
import io
import time
import logging
import threading
from typing import Optional

import requests

logger = logging.getLogger("capgainsiq.nse_search")

# ─── In-memory cache ──────────────────────────────────────────────────────────
_cache: dict = {
    "stocks":    [],       # list of {symbol, company_name, _tokens}
    "loaded_at": 0.0,
    "lock":      threading.Lock(),
}

_CACHE_TTL   = 86_400   # 24 hours
_NSE_CSV_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

# NSE requires a browser-like User-Agent otherwise returns 403
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.nseindia.com/",
}

# ─── Stop words for tokenisation ─────────────────────────────────────────────
_STOP = {
    "limited", "ltd", "corporation", "corp", "industries",
    "enterprises", "company", "co", "and", "the", "of", "for",
    "a", "an", "india", "indian", "private", "pvt", "public",
    "&", "-", "–", "infrastructure",
}


def _tokenize(name: str) -> set[str]:
    """
    Normalise a company name into meaningful tokens.
    "ADANI ENTERPRISES LIMITED" → {"adani"}
    "TATA STEEL LIMITED"        → {"tata", "steel"}
    "HDFC BANK LIMITED"         → {"hdfc", "bank"}
    """
    tokens = re.findall(r"[a-zA-Z0-9]+", name.lower())
    return {t for t in tokens if t not in _STOP and len(t) > 1}


def _load_stock_list(force: bool = False) -> list[dict]:
    """
    Load NSE equity list from official CSV. Thread-safe with memory cache.
    """
    with _cache["lock"]:
        age = time.time() - _cache["loaded_at"]
        if not force and _cache["stocks"] and age < _CACHE_TTL:
            return _cache["stocks"]

        try:
            logger.info("Fetching NSE equity list from nsearchives.nseindia.com...")

            # NSE needs a session with Referer/User-Agent
            session = requests.Session()
            session.headers.update(_HEADERS)

            # First hit the main site to get cookies
            try:
                session.get("https://www.nseindia.com", timeout=10)
            except Exception:
                pass  # best-effort cookie grab

            r = session.get(_NSE_CSV_URL, timeout=15)
            r.raise_for_status()

            # Parse CSV — columns: SYMBOL, NAME OF COMPANY, SERIES, ...
            import csv
            reader = csv.DictReader(io.StringIO(r.text))

            stocks = []
            for row in reader:
                symbol = (row.get("SYMBOL") or row.get("Symbol") or "").strip().upper()
                name   = (row.get("NAME OF COMPANY") or row.get("Company Name") or "").strip()
                series = (row.get("SERIES") or "").strip().upper()

                if not symbol or not name:
                    continue

                # Keep only EQ series (main board equity) — skip BE, SM, ST etc.
                # But don't filter too strictly — some good stocks are in other series
                stocks.append({
                    "symbol":       symbol,
                    "company_name": name,
                    "series":       series,
                    "nse_ticker":   f"{symbol}.NS",
                    "_tokens":      _tokenize(name),
                    "_sym_tokens":  _tokenize(symbol),
                })

            _cache["stocks"]    = stocks
            _cache["loaded_at"] = time.time()
            logger.info(f"NSE stock list loaded: {len(stocks)} stocks cached.")
            return stocks

        except Exception as e:
            logger.error(f"Failed to load NSE stock list: {e}")
            return _cache["stocks"]  # return stale if available


# ─── Scoring ──────────────────────────────────────────────────────────────────

def _score(query_tokens: set[str], stock: dict) -> float:
    """
    Score a query against a stock entry.
    Checks both company name tokens and symbol tokens.
    """
    if not query_tokens:
        return 0.0

    name_tokens = stock["_tokens"]
    sym_tokens  = stock["_sym_tokens"]
    all_tokens  = name_tokens | sym_tokens

    if not all_tokens:
        return 0.0

    intersection = query_tokens & all_tokens

    # Query coverage — how many user words found?
    query_cov = len(intersection) / len(query_tokens)

    # Name coverage — how specific is the match?
    name_cov  = len(intersection) / len(all_tokens) if all_tokens else 0.0

    # Exact symbol match bonus — if user typed "RELIANCE" and symbol is "RELIANCE"
    sym_bonus = 0.0
    if query_tokens == sym_tokens:
        sym_bonus = 0.3
    elif query_tokens <= sym_tokens or sym_tokens <= query_tokens:
        sym_bonus = 0.15

    score = 0.65 * query_cov + 0.2 * name_cov + sym_bonus
    return min(score, 1.0)


# ─── Public API ───────────────────────────────────────────────────────────────

def search_stocks(query: str, top_n: int = 5) -> list[dict]:
    """
    Search NSE stocks by company name or symbol.

    Returns top N matches:
    [{ symbol, company_name, nse_ticker, bse_ticker, score }]
    """
    if not query or not query.strip():
        return []

    query_tok = _tokenize(query.strip())
    if not query_tok:
        return []

    stocks  = _load_stock_list()
    scored  = []

    for stock in stocks:
        score = _score(query_tok, stock)
        if score >= 0.2:
            scored.append({
                "symbol":       stock["symbol"],
                "company_name": stock["company_name"],
                "nse_ticker":   stock["nse_ticker"],
                "bse_ticker":   f"{stock['symbol']}.BO",
                "score":        round(score, 3),
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_n]


def auto_match_stock(name: str, min_score: float = 0.50) -> Optional[dict]:
    """
    Auto-match a company name to NSE ticker.
    Returns best match if confidence >= min_score, else None.

    Used during Zerodha import to fix incorrect full-name tickers.
    """
    matches = search_stocks(name, top_n=1)
    if matches and matches[0]["score"] >= min_score:
        return matches[0]
    return None


def bulk_match_stocks(names: list[str], min_score: float = 0.50) -> dict[str, dict]:
    """
    Bulk auto-match company names to NSE tickers.
    Returns dict: { name → match_result }

    match_result:
      { matched: True,  symbol, company_name, nse_ticker, score }
      { matched: False }
    """
    results = {}
    for name in names:
        if not name or not name.strip():
            continue
        match = auto_match_stock(name.strip(), min_score=min_score)
        if match:
            results[name] = {"matched": True, **match}
        else:
            results[name] = {"matched": False}
    return results


def warmup():
    """Pre-load NSE stock list at startup in background thread."""
    t = threading.Thread(target=_load_stock_list, daemon=True)
    t.start()
    logger.info("NSE stock list warmup started in background.")

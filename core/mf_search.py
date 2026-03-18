"""
core/mf_search.py
─────────────────────────────────────────────────────────────────────────────
Mutual Fund name → AMFI scheme code resolver.

Uses the public mfapi.in registry:
  GET https://api.mfapi.in/mf          → full list of all ~1500+ funds
  GET https://api.mfapi.in/mf/search?q → search by name

Strategy:
  1. On first request, fetch the full fund list from mfapi.in and cache in memory
  2. Fuzzy-match a fund name against the cached list using token-based scoring
  3. Return ranked matches with confidence scores

No external fuzzy-matching libraries needed — simple token overlap works well
for Indian MF names which follow predictable naming conventions.
─────────────────────────────────────────────────────────────────────────────
"""

import re
import time
import logging
import threading
from typing import Optional

import requests

logger = logging.getLogger("capgainsiq.mf_search")

# ─── In-memory cache ──────────────────────────────────────────────────────────
_cache: dict = {
    "funds":      [],        # list of {scheme_code, scheme_name}
    "loaded_at":  0.0,       # unix timestamp
    "lock":       threading.Lock(),
}

# Refresh cache every 24 hours
_CACHE_TTL_SECONDS = 86_400
_MFAPI_LIST_URL    = "https://api.mfapi.in/mf"
_MFAPI_SEARCH_URL  = "https://api.mfapi.in/mf/search"


def _load_fund_list(force: bool = False) -> list[dict]:
    """
    Load full fund list from mfapi.in into memory cache.
    Thread-safe. Returns cached list if fresh enough.
    """
    with _cache["lock"]:
        age = time.time() - _cache["loaded_at"]
        if not force and _cache["funds"] and age < _CACHE_TTL_SECONDS:
            return _cache["funds"]

        try:
            logger.info("Fetching full MF list from mfapi.in...")
            r = requests.get(_MFAPI_LIST_URL, timeout=15)
            r.raise_for_status()
            raw = r.json()  # list of {schemeCode, schemeName}

            funds = [
                {
                    "scheme_code": str(item["schemeCode"]),
                    "scheme_name": item["schemeName"],
                    # Pre-compute normalised tokens for faster matching
                    "_tokens": _tokenize(item["schemeName"]),
                }
                for item in raw
                if item.get("schemeCode") and item.get("schemeName")
            ]

            _cache["funds"]     = funds
            _cache["loaded_at"] = time.time()
            logger.info(f"MF list loaded: {len(funds)} funds cached.")
            return funds

        except Exception as e:
            logger.error(f"Failed to load MF list: {e}")
            return _cache["funds"]   # return stale if available


# ─── Tokenisation helpers ─────────────────────────────────────────────────────

# Common words to ignore when matching — they add noise
_STOP_WORDS = {
    "fund", "scheme", "plan", "option", "growth", "direct", "regular",
    "dividend", "payout", "reinvestment", "idcw", "the", "of", "and",
    "for", "a", "an", "-", "–", "&",
}

def _tokenize(name: str) -> set[str]:
    """
    Convert a fund name to a set of meaningful tokens.
    'HDFC Nifty 50 Index Fund - Direct Plan - Growth' →
    {'hdfc', 'nifty', '50', 'index'}
    """
    tokens = re.findall(r"[a-zA-Z0-9]+", name.lower())
    return {t for t in tokens if t not in _STOP_WORDS and len(t) > 1}


def _score(query_tokens: set[str], fund_tokens: set[str]) -> float:
    """
    Jaccard-style token overlap score between 0.0 and 1.0.
    Weighted to favour query coverage (we care more that the user's
    query words are found in the fund name than vice versa).
    """
    if not query_tokens or not fund_tokens:
        return 0.0

    intersection = query_tokens & fund_tokens

    # Query coverage: how many of the user's words matched?
    query_coverage  = len(intersection) / len(query_tokens)

    # Fund coverage: how specific is the match?
    fund_coverage   = len(intersection) / len(fund_tokens)

    # Weighted blend: prioritise query coverage
    return 0.7 * query_coverage + 0.3 * fund_coverage


# ─── Public API ───────────────────────────────────────────────────────────────

def search_funds(query: str, top_n: int = 5) -> list[dict]:
    """
    Search funds by name. Returns top N matches with scores.

    Each result:
    {
        "scheme_code": "118989",
        "scheme_name": "HDFC Nifty 50 Index Fund - Direct Plan - Growth Option",
        "score":       0.87        # 0.0 – 1.0 confidence
    }

    Strategy:
    1. Try mfapi.in /search endpoint first (fast, server-side)
    2. Then re-rank results using our local fuzzy scorer
    3. Also run against local cache for any matches mfapi might miss
    """
    if not query or not query.strip():
        return []

    query       = query.strip()
    query_tok   = _tokenize(query)
    results     = {}   # scheme_code → best result dict

    # ── Step 1: mfapi.in search ───────────────────────────────────────────────
    try:
        r = requests.get(
            _MFAPI_SEARCH_URL,
            params={"q": query},
            timeout=8
        )
        if r.ok:
            for item in r.json():
                code  = str(item.get("schemeCode", ""))
                name  = item.get("schemeName", "")
                if not code or not name:
                    continue
                score = _score(query_tok, _tokenize(name))
                if code not in results or results[code]["score"] < score:
                    results[code] = {
                        "scheme_code": code,
                        "scheme_name": name,
                        "score":       round(score, 3),
                    }
    except Exception as e:
        logger.warning(f"mfapi search failed for '{query}': {e}")

    # ── Step 2: local cache fuzzy match ───────────────────────────────────────
    funds = _load_fund_list()
    for fund in funds:
        score = _score(query_tok, fund["_tokens"])
        if score < 0.25:          # skip low-confidence matches
            continue
        code = fund["scheme_code"]
        if code not in results or results[code]["score"] < score:
            results[code] = {
                "scheme_code": code,
                "scheme_name": fund["scheme_name"],
                "score":       round(score, 3),
            }

    # ── Step 3: sort and return top N ─────────────────────────────────────────
    ranked = sorted(results.values(), key=lambda x: x["score"], reverse=True)
    return ranked[:top_n]


def get_fund_by_code(scheme_code: str) -> Optional[dict]:
    """
    Lookup a fund by its exact scheme code.
    Returns {scheme_code, scheme_name} or None.
    """
    funds = _load_fund_list()
    for fund in funds:
        if fund["scheme_code"] == str(scheme_code):
            return {
                "scheme_code": fund["scheme_code"],
                "scheme_name": fund["scheme_name"],
            }
    return None


def auto_match_fund(name: str, min_score: float = 0.55) -> Optional[dict]:
    """
    Try to automatically match a fund name to a scheme code.
    Returns best match only if confidence >= min_score, else None.

    Used during Zerodha CSV import to auto-fill MF tickers.
    """
    matches = search_funds(name, top_n=1)
    if matches and matches[0]["score"] >= min_score:
        return matches[0]
    return None


def warmup():
    """
    Pre-load the fund list in the background at startup.
    Prevents first-request latency.
    """
    import threading
    t = threading.Thread(target=_load_fund_list, daemon=True)
    t.start()
    logger.info("MF fund list warmup started in background.")

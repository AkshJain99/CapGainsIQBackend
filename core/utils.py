"""
core/utils.py
Shared utilities used by all 3 tools.
Price fetching, date parsing, XIRR, Indian FY helpers.
"""

import math
import logging
import warnings
from datetime import datetime, date
from typing import Optional

import numpy as np
import pandas as pd
import requests

warnings.filterwarnings("ignore")
logger = logging.getLogger("capgainsiq.utils")


# ─── Date helpers ─────────────────────────────────────────────────────────────

def parse_date(date_str: str) -> pd.Timestamp:
    """Parse DD-MM-YYYY or YYYY-MM-DD to Timestamp."""
    for fmt in ["%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y"]:
        try:
            return pd.Timestamp(datetime.strptime(date_str.strip(), fmt))
        except ValueError:
            continue
    raise ValueError(f"Cannot parse date: '{date_str}'")


def get_fy(dt: pd.Timestamp) -> str:
    """
    Return Indian financial year string.
    April 2023 → '2023-24', January 2024 → '2023-24'
    Mirrors get_fy() from original notebook exactly.
    """
    if dt.month > 3:
        return f"{dt.year}-{str(dt.year + 1)[2:]}"
    return f"{dt.year - 1}-{str(dt.year)[2:]}"


def fy_start_year(fy_str: str) -> int:
    """Extract start calendar year from FY string like '2023-24' → 2023."""
    return int(fy_str.split("-")[0])


def current_fy() -> str:
    return get_fy(pd.Timestamp(date.today()))


# ─── Tax calculation ──────────────────────────────────────────────────────────

def calc_indian_tax(ltcg: float, stcg: float, fy: str) -> dict:
    """
    Calculate Indian capital gains tax.
    Mirrors Indian IT Act rules exactly.

    Pre FY2023-24:  LTCG 10% above ₹1,00,000 | STCG 15% | Debt 3yr threshold
    FY2023-24:      LTCG 10% above ₹1,00,000 | STCG 15% | Debt always STCG
    FY2024-25+:     LTCG 12.5% above ₹1,25,000 | STCG 20% | Gold FoF 24 months
    """
    start = fy_start_year(fy)
    if start < 2023:
        tax_l = max(0.0, (ltcg - 100_000) * 0.10)
        tax_s = max(0.0, stcg * 0.15)
        return {"tax_l": tax_l, "tax_s": tax_s,
                "exemption": 100_000, "ltcg_rate": 10, "stcg_rate": 15}
    elif start == 2023:
        tax_l = max(0.0, (ltcg - 100_000) * 0.10)
        tax_s = max(0.0, stcg * 0.15)
        return {"tax_l": tax_l, "tax_s": tax_s,
                "exemption": 100_000, "ltcg_rate": 10, "stcg_rate": 15}
    else:
        tax_l = max(0.0, (ltcg - 125_000) * 0.125)
        tax_s = max(0.0, stcg * 0.20)
        return {"tax_l": tax_l, "tax_s": tax_s,
                "exemption": 125_000, "ltcg_rate": 12.5, "stcg_rate": 20}


def get_ltcg_threshold(asset_class: str, fy: str) -> int:
    """
    Get the correct LTCG holding period threshold in days
    for an asset class in a given financial year.

    Rules:
      EQUITY / MF (equity-oriented):
        Always 365 days (1 year)

      DEBT MF (post April 1, 2023 — Budget 2023):
        ALL gains are STCG regardless of holding period
        Return 99999 (effectively never LTCG)

      DEBT MF (pre April 1, 2023):
        1095 days (3 years)

      COMMODITY (Gold/Silver FoF, post Budget 2024):
        730 days (24 months)

      COMMODITY (pre Budget 2024):
        1095 days (36 months)
    """
    cls   = asset_class.upper()
    start = fy_start_year(fy)

    if cls in ("EQUITY", "MF"):
        return 365

    if cls == "DEBT":
        # Budget 2023 (FY2023-24 onwards): debt MF always STCG
        return 99999 if start >= 2023 else 1095

    if cls == "COMMODITY":
        # Budget 2024 (FY2024-25 onwards): Gold/Silver FoF = 24 months
        return 730 if start >= 2024 else 1095

    return 365  # default fallback


# ─── XIRR ─────────────────────────────────────────────────────────────────────

def safe_xirr(cashflows: list[tuple]) -> float:
    """
    Calculate XIRR safely. Returns percentage (e.g. 15.3 for 15.3%).
    cashflows = list of (date, amount) tuples.
    Mirrors safe_xirr() from original notebook.
    """
    if len(cashflows) < 2:
        return 0.0
    try:
        from pyxirr import xirr
        dates   = [cf[0] for cf in cashflows]
        amounts = [cf[1] for cf in cashflows]
        result  = xirr(dates, amounts)
        if result is None or not math.isfinite(float(result)):
            return 0.0
        return round(float(result) * 100, 4)
    except Exception as e:
        logger.debug(f"XIRR failed: {e}")
        return 0.0


# ─── Price fetching ───────────────────────────────────────────────────────────

def fetch_latest_price(ticker: str, source: str, asset_name: str = "") -> float:
    """
    Fetch latest price from Yahoo Finance or MF API (mfapi.in).
    Mirrors get_latest_price() from original notebook exactly.
    Returns 0.0 on any failure.
    """
    if not ticker:
        return 0.0

    try:
        src = source.upper()

        if src == "YF":
            import yfinance as yf
            t = yf.Ticker(ticker)

            # Try fast_info first (no network if cached)
            try:
                price = t.fast_info.last_price
                if price and math.isfinite(float(price)) and float(price) > 0:
                    return round(float(price), 4)
            except Exception:
                pass

            # Fallback: history
            hist = t.history(period="5d")
            if not hist.empty:
                return round(float(hist["Close"].iloc[-1]), 4)

        elif src == "MF":
            if not str(ticker).isdigit():
                logger.warning(f"MF ticker must be numeric: {ticker}")
                return 0.0
            r = requests.get(
                f"https://api.mfapi.in/mf/{ticker}",
                timeout=10
            )
            if r.ok:
                data = r.json().get("data", [])
                if data:
                    return round(float(data[0]["nav"]), 4)
            else:
                logger.warning(
                    f"MF API {asset_name}: status {r.status_code}"
                )

    except Exception as e:
        logger.warning(
            f"Price fetch failed [{asset_name} | {ticker} | {source}]: "
            f"{type(e).__name__}: {e}"
        )

    return 0.0


# ─── Numeric helpers ──────────────────────────────────────────────────────────

def safe_float(val, default: float = 0.0) -> float:
    """Safely convert any value to float."""
    try:
        f = float(val)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def clean_numeric(val) -> float:
    """
    Safely parse a value to float, handling currency symbols and whitespace.
    Mirrors clean_numeric() from original notebook.
    """
    if isinstance(val, (int, float)):
        return float(val)
    import re
    cleaned = str(val).encode("ascii", "ignore").decode("ascii") if val is not None else ""
    cleaned = re.sub(r"[^\d.]", "", cleaned) if cleaned else ""
    return float(cleaned) if cleaned else 0.0

"""
tools/capgains.py
─────────────────────────────────────────────────────────────────────────────
Tool 3 — Capital Gains Calculator
Source: momentum_strategy_v3.2.1.py (bottom cell — INVESTMENT CAPITAL GAIN)

HOW TO UPDATE:
  When dad updates the Colab notebook, copy the capital gains cell here.
  Only strip these Colab-specific lines:
    - from google.colab import auth, userdata, drive
    - drive.mount(...)
    - auth.authenticate_user()
    - creds, _ = default()
    - gc = gspread.authorize(creds)
    - SPREADSHEET_URL = userdata.get(...)
    - spreadsheet = gc.open_by_url(...)
    - ws.update(...) / write_sheet(...) calls
  Everything else stays EXACTLY as dad wrote it.
─────────────────────────────────────────────────────────────────────────────
"""

import math
import re
import logging
import warnings
from collections import deque, defaultdict
from datetime import datetime, date
from typing import Optional, Callable

import numpy as np
import pandas as pd
import requests

from core.utils import (
    parse_date, get_fy, fy_start_year, calc_indian_tax,
    get_ltcg_threshold, safe_xirr, fetch_latest_price, clean_numeric,
)

warnings.filterwarnings("ignore")
logger = logging.getLogger("capgainsiq.capgains")

# ─── Constants (from original notebook) ──────────────────────────────────────
EPSILON = 1e-6   # floating-point dust threshold — from original notebook

# NOTE: DEFAULT_THRESHOLDS removed — use get_ltcg_threshold(asset_class, fy)
# which correctly handles Budget 2023 (debt always STCG) and
# Budget 2024 (Gold/Silver FoF 24 months) rules.


# ─── Helpers (from original notebook, unchanged) ─────────────────────────────

def sanitize_value(v):
    """
    Convert numpy/pandas types to native Python types safe for serialization.
    From original notebook — sanitize_value()
    """
    if isinstance(v, (np.int64, np.int32)):
        return int(v)
    if isinstance(v, (float, np.float64, np.float32)):
        if math.isnan(v) or math.isinf(v):
            return None
        return float(v)
    if isinstance(v, pd.Timestamp):
        return v.strftime("%d-%m-%Y")
    return v


def standardize_asset_name(name: str) -> str:
    """
    Standardize asset names.
    From original notebook — standardize_asset_name()
    """
    name = str(name).upper().strip()
    name = (name
            .replace(" LIMITED", "")
            .replace(" LTD", "")
            .replace(" CORPORATION", "")
            .replace(" CORP", ""))
    return name


def get_threshold(config_lookup: dict, fy: str, asset_cls: str) -> int:
    """
    Get holding period threshold for LTCG classification.

    Priority:
    1. User-provided config_lookup (from FYConfig in request)
    2. Correct Indian tax rules via get_ltcg_threshold()

    This correctly handles:
    - Budget 2023: Debt MF always STCG (threshold = 99999)
    - Budget 2024: Gold/Silver FoF = 730 days (24 months)
    - Equity: always 365 days
    """
    cls = asset_cls.upper()

    # Check user config first
    fy_config = config_lookup.get(fy, {})
    if fy_config:
        if cls == "EQUITY" and fy_config.get("EQUITY"):
            return int(fy_config["EQUITY"])
        if cls == "DEBT" and fy_config.get("DEBT"):
            return int(fy_config["DEBT"])
        if cls == "COMMODITY" and fy_config.get("COMMODITY"):
            return int(fy_config["COMMODITY"])
        if cls == "MF" and fy_config.get("EQUITY"):
            return int(fy_config["EQUITY"])

    # Use correct statutory rules
    threshold = get_ltcg_threshold(cls, fy)
    if threshold == 99999:
        logger.debug(
            f"Asset class {cls} in FY {fy}: "
            f"Budget 2023 rule — DEBT MF always STCG."
        )
    return threshold


# ─── Main function ─────────────────────────────────────────────────────────────

def run_capital_gains(
    assets_input:       list[dict],
    transactions_input: list[dict],
    config_input:       list[dict],
    progress_callback:  Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Main capital gains calculation function.

    Adapted from the 'INVESTMENT CAPITAL GAIN CALCULATION' cell
    in momentum_strategy_v3.2.1.py

    Original functions used:
      - sanitize_value()
      - clean_numeric()
      - get_fy()
      - get_latest_price()  → now fetch_latest_price() in core/utils.py
      - get_threshold()
      - FIFO Logic section
      - Aggregation section
      - FY summary rows section

    Colab-specific parts removed:
      - gspread auth + write_sheet() calls
      - SPREADSHEET_URL / gc references
      - apply_range_formatting() (UI only)

    Args:
        assets_input:       list of asset dicts {asset_name, asset_class, ticker, source}
        transactions_input: list of transaction dicts matching Transaction model
        config_input:       list of FY config dicts {financial_year, equity_threshold, ...}
        progress_callback:  optional function(str) called with progress messages

    Returns:
        dict matching CapGainsResult model
    """

    def _prog(msg: str):
        if progress_callback:
            progress_callback(msg)
        logger.info(msg)

    warnings_list: list[str] = []
    today = pd.Timestamp(date.today())

    # ── 1. Build asset lookup ─────────────────────────────────────────────────
    # Mirrors: assets_df.set_index('Asset Name').to_dict('index')
    assets_map: dict[str, dict] = {}
    for a in assets_input:
        name = standardize_asset_name(a.get("asset_name", ""))
        assets_map[name] = {
            "Asset Class": a.get("asset_class", "EQUITY").upper(),
            "Ticker":      a.get("ticker", ""),
            "Source":      a.get("source", "YF").upper(),
        }

    # ── 2. Build FY config lookup ─────────────────────────────────────────────
    # Mirrors: config_df.set_index('Financial Year').to_dict('index')
    config_lookup: dict[str, dict] = {}
    for c in config_input:
        fy = c.get("financial_year", "")
        config_lookup[fy] = {
            "EQUITY":    c.get("equity_threshold",    365),
            "DEBT":      c.get("debt_threshold",     1095),
            "COMMODITY": c.get("commodity_threshold",1095),
            "MF":        c.get("equity_threshold",    365),
        }

    # ── 3. Parse transactions ─────────────────────────────────────────────────
    # Mirrors: trans_raw processing block
    _prog("Parsing transactions...")
    parsed_txs: list[dict] = []

    for t in transactions_input:
        try:
            asset_name = standardize_asset_name(t.get("asset_name", ""))
            dt         = parse_date(str(t.get("date", "")))
            qty        = clean_numeric(t.get("quantity", 0))
            rate       = clean_numeric(t.get("rate", 0))
            amount     = clean_numeric(t.get("amount", 0)) or round(qty * rate, 2)
            charges    = clean_numeric(t.get("total_charges", 0))
            tr_type    = str(t.get("tr_type", "Buy")).strip()

            if qty <= 0 or rate <= 0:
                warnings_list.append(
                    f"Skipped [{asset_name} {t.get('date','')}]: "
                    f"qty={qty} rate={rate} must be > 0"
                )
                continue

            parsed_txs.append({
                "asset_name":    asset_name,
                "date":          dt,
                "tr_type":       tr_type,
                "rate":          rate,
                "quantity":      qty,
                "amount":        amount,
                "total_charges": charges,
            })
        except Exception as e:
            warnings_list.append(
                f"Skipped transaction "
                f"[{t.get('asset_name','?')} {t.get('date','?')}]: {e}"
            )

    if not parsed_txs:
        return _empty_result("No valid transactions found.", warnings_list)

    # ── 4. Sort transactions ──────────────────────────────────────────────────
    # Mirrors: con.execute("SELECT * FROM df_t ORDER BY Asset Name, Date, ...")
    parsed_txs.sort(key=lambda x: (
        x["asset_name"],
        x["date"],
        0 if x["tr_type"].lower().startswith("buy") else 1,
    ))

    unique_assets = list(dict.fromkeys(t["asset_name"] for t in parsed_txs))
    _prog(f"Processing {len(unique_assets)} assets...")

    # ── 5. Fetch latest prices ────────────────────────────────────────────────
    # Mirrors: get_latest_price() calls in portfolio loop
    prices: dict[str, float] = {}
    for i, name in enumerate(unique_assets):
        info   = assets_map.get(name, {})
        ticker = info.get("Ticker", "")
        source = info.get("Source", "YF")
        _prog(f"Fetching price {i+1}/{len(unique_assets)}: {name}")
        p = fetch_latest_price(ticker, source, name)
        prices[name] = p
        if p == 0.0:
            warnings_list.append(
                f"Could not fetch live price for {name} "
                f"(ticker: {ticker}). Unrealised gains will show ₹0."
            )

    # ── 6. FIFO Logic ─────────────────────────────────────────────────────────
    # Directly from: 'FIFO Logic' section of original notebook
    # Variable names kept identical to original where possible.

    fy_gains: dict[str, dict] = defaultdict(
        lambda: {"intra": 0.0, "stcg": 0.0, "ltcg": 0.0}
    )
    portfolio_rows:    list[dict]  = []
    all_port_cashflows: list[tuple] = []

    for asset in unique_assets:
        info        = assets_map.get(asset, {})
        asset_cls   = info.get("Asset Class", "EQUITY")
        ticker      = info.get("Ticker", "")
        price       = prices.get(asset, 0.0)
        df_asset    = [t for t in parsed_txs if t["asset_name"] == asset]

        # buy_queue: FIFO lot queue — exactly as original
        buy_queue: deque = deque()

        rem_units   = 0.0
        r_intra     = 0.0
        r_stcg      = 0.0
        r_ltcg      = 0.0
        asset_cashflows: list[tuple] = []
        total_charges_asset = 0.0

        for tx in df_asset:
            dt      = tx["date"]
            qty     = tx["quantity"]
            rate    = tx["rate"]
            amt     = tx["amount"]
            charges = tx["total_charges"]
            fy      = get_fy(dt)
            threshold_days = get_threshold(config_lookup, fy, asset_cls)
            total_charges_asset += charges

            is_buy = tx["tr_type"].lower().startswith("buy")

            if is_buy:
                # Mirrors original: buy_queue.append({'date': dt, 'qty': qty, 'rate': rate})
                buy_queue.append({"date": dt, "qty": qty, "rate": rate})
                rem_units += qty
                asset_cashflows.append((dt.date(), -(amt + charges)))

            else:
                # Sell — mirrors original sell logic exactly
                asset_cashflows.append((dt.date(), (amt - charges)))
                sell_qty  = qty
                rem_units -= qty

                while sell_qty > EPSILON and buy_queue:
                    lot   = buy_queue[0]
                    match = min(sell_qty, lot["qty"])

                    # Proportional sell charges per lot
                    # Mirrors: sell_charges_allocated = charges * (match / qty)
                    sell_charges_allocated = (
                        charges * (match / qty) if qty > 0 else 0.0
                    )

                    # Gain calculation — mirrors original exactly
                    gain = match * (rate - lot["rate"]) - sell_charges_allocated

                    days = (dt - lot["date"]).days

                    # Classification — mirrors original exactly
                    if days <= 0:
                        r_intra += gain
                        fy_gains[fy]["intra"] += gain
                    elif days <= threshold_days:
                        r_stcg += gain
                        fy_gains[fy]["stcg"] += gain
                    else:
                        r_ltcg += gain
                        fy_gains[fy]["ltcg"] += gain

                    lot["qty"] -= match
                    sell_qty   -= match

                    # EPSILON guard — from original notebook comments
                    if lot["qty"] < EPSILON:
                        buy_queue.popleft()

                # Unmatched sell warning — mirrors original
                if sell_qty > EPSILON:
                    warnings_list.append(
                        f"{asset}: {sell_qty:.4f} unmatched sell units on "
                        f"{dt.date()} — possible missing buy transactions."
                    )

        # ── Unrealised gains ──────────────────────────────────────────────────
        # Mirrors: u_stcg / u_ltcg calculation in original
        cur_fy    = get_fy(today)
        u_thresh  = get_threshold(config_lookup, cur_fy, asset_cls)
        u_stcg    = 0.0
        u_ltcg    = 0.0

        if price > 0:
            for lot in buy_queue:
                lot_gain  = lot["qty"] * (price - lot["rate"])
                held_days = (today - lot["date"]).days
                if held_days <= u_thresh:
                    u_stcg += lot_gain
                else:
                    u_ltcg += lot_gain

        cur_val = rem_units * price if price > 0 else 0.0

        # Terminal cashflow for XIRR — mirrors original
        if rem_units > 0 and cur_val > 0:
            asset_cashflows.append((today.date(), cur_val))

        # XIRR — mirrors: xirr(asset_cashflows) * 100
        a_xirr = safe_xirr(asset_cashflows) if len(asset_cashflows) >= 2 else 0.0

        # Contribute to portfolio cashflows (exclude terminal)
        all_port_cashflows.extend(
            cf for cf in asset_cashflows if cf[0] != today.date()
        )

        portfolio_rows.append({
            "asset_name":              asset,
            "asset_class":             asset_cls,
            "ticker":                  ticker,
            "latest_price":            round(price, 4),
            "remaining_units":         round(rem_units, 6),
            "current_portfolio_value": round(cur_val, 2),
            "intraday_cg":             round(r_intra, 2),
            "r_ltcg":                  round(r_ltcg,  2),
            "r_stcg":                  round(r_stcg,  2),
            "r_total":                 round(r_ltcg + r_stcg, 2),
            "u_ltcg":                  round(u_ltcg,  2),
            "u_stcg":                  round(u_stcg,  2),
            "u_total":                 round(u_ltcg + u_stcg, 2),
            "xirr":                    round(a_xirr, 4),
            "total_charges":           round(total_charges_asset, 2),
            "is_subtotal":             False,
            "is_grand_total":          False,
        })

    # ── 7. Portfolio XIRR ─────────────────────────────────────────────────────
    # Mirrors: p_xirr = xirr(all_port_cashflows) * 100
    total_cur_val = sum(r["current_portfolio_value"] for r in portfolio_rows)
    if total_cur_val > 0:
        all_port_cashflows.append((today.date(), total_cur_val))
    portfolio_xirr = (
        safe_xirr(all_port_cashflows)
        if len(all_port_cashflows) >= 2 else 0.0
    )

    # ── 8. Aggregation — subtotals per asset class ────────────────────────────
    # Mirrors: for cls, grp in df_res.groupby('Asset Class') block
    _prog("Aggregating results...")
    final_rows:      list[dict] = []
    subtotal_keys = ["current_portfolio_value", "intraday_cg", "r_ltcg",
                     "r_stcg", "r_total", "u_ltcg", "u_stcg", "u_total",
                     "total_charges"]

    classes = list(dict.fromkeys(r["asset_class"] for r in portfolio_rows))

    for cls in classes:
        cls_rows = [r for r in portfolio_rows if r["asset_class"] == cls]
        final_rows.extend(cls_rows)

        # Only add subtotal row if more than 1 asset in this class
        if len(cls_rows) > 1:
            sub: dict = {k: 0.0 for k in subtotal_keys}
            for k in subtotal_keys:
                sub[k] = round(sum(r[k] for r in cls_rows), 2)
            sub.update({
                "asset_name":    f"Subtotal — {cls}",
                "asset_class":   cls,
                "ticker":        "",
                "latest_price":  0.0,
                "remaining_units": 0.0,
                "xirr":          0.0,
                "is_subtotal":   True,
                "is_grand_total":False,
            })
            final_rows.append(sub)

    # Grand total row — mirrors original grand dict
    grand: dict = {k: 0.0 for k in subtotal_keys}
    for k in subtotal_keys:
        grand[k] = round(sum(r[k] for r in portfolio_rows), 2)
    grand.update({
        "asset_name":     "GRAND TOTAL",
        "asset_class":    "",
        "ticker":         "",
        "latest_price":   0.0,
        "remaining_units":0.0,
        "xirr":           round(portfolio_xirr, 4),
        "is_subtotal":    False,
        "is_grand_total": True,
    })
    final_rows.append(grand)

    # ── 9. FY breakdown ───────────────────────────────────────────────────────
    # Mirrors: fy_data_rows block in original
    fy_breakdown = []
    for fy_str in sorted(fy_gains.keys()):
        g = fy_gains[fy_str]
        fy_breakdown.append({
            "financial_year": fy_str,
            "intraday_cg":    round(g["intra"], 2),
            "r_stcg":         round(g["stcg"],  2),
            "r_ltcg":         round(g["ltcg"],  2),
            "total_cg":       round(g["stcg"] + g["ltcg"], 2),
        })

    # ── 10. Summary ───────────────────────────────────────────────────────────
    total_invested = sum(
        t["amount"] + t["total_charges"]
        for t in parsed_txs
        if t["tr_type"].lower().startswith("buy")
    )

    summary = {
        "total_invested":       round(total_invested, 2),
        "current_value":        round(total_cur_val, 2),
        "total_realised_pnl":   round(grand["r_total"], 2),
        "total_unrealised_pnl": round(grand["u_total"], 2),
        "overall_xirr":         round(portfolio_xirr, 4),
        "total_charges":        round(grand["total_charges"], 2),
        "r_ltcg":               round(grand["r_ltcg"], 2),
        "r_stcg":               round(grand["r_stcg"], 2),
        "r_intraday":           round(grand["intraday_cg"], 2),
        "u_ltcg":               round(grand["u_ltcg"], 2),
        "u_stcg":               round(grand["u_stcg"], 2),
    }

    _prog("Done.")
    return {
        "summary":       summary,
        "capital_gains": final_rows,
        "fy_breakdown":  fy_breakdown,
        "warnings":      warnings_list,
        "computed_at":   datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def _empty_result(error_msg: str, warnings: list) -> dict:
    """Return empty result structure on fatal error."""
    return {
        "summary": {
            "total_invested": 0, "current_value": 0,
            "total_realised_pnl": 0, "total_unrealised_pnl": 0,
            "overall_xirr": 0, "total_charges": 0,
            "r_ltcg": 0, "r_stcg": 0, "r_intraday": 0,
            "u_ltcg": 0, "u_stcg": 0,
        },
        "capital_gains": [],
        "fy_breakdown":  [],
        "warnings":      [error_msg] + warnings,
        "computed_at":   datetime.now().strftime("%Y-%m-%d %H:%M"),
    }

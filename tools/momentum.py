"""
tools/momentum.py
─────────────────────────────────────────────────────────────────────────────
Tool 1 — Momentum Strategy Engine
Source: momentum_strategy_v3.2.1.py (Modules 01–14)

STATUS: Placeholder — not yet implemented.

HOW TO IMPLEMENT:
  1. Copy Modules 01–14 from the Colab notebook into this file.
  2. Strip these Colab-specific parts:
       - from google.colab import auth, userdata, drive
       - drive.mount(...)
       - auth.authenticate_user()
       - gspread auth / GSpreadManager (all Google Sheets write calls)
       - FRED_API_KEY = userdata.get(...)
       - SPREADSHEET_URL = userdata.get(...)
       - Module 02 Colab imports (keep the logic imports)
       - _check_packages() / subprocess pip installs
  3. Keep ALL strategy logic exactly:
       - DataManager (DuckDB)
       - build_price_universe()
       - compute_monthly()
       - run_backtest()
       - live_dashboard()  → return as dict instead of writing to sheets
       - plot_institutional_dashboard() → return chart data, not plt.show()
  4. Function signature:
       run_momentum_pipeline(config: dict, assets: list) -> dict

KEY FUNCTIONS FROM ORIGINAL TO KEEP (unchanged):
  - get_dynamic_target_vol()
  - check_trailing_stop()
  - ensemble_ranks()
  - allocation()
  - _cap_and_redistribute_weights()
  - _compute_vol_scale()
  - _compute_partial_regime_allocation()
─────────────────────────────────────────────────────────────────────────────
"""

from typing import Optional, Callable
import logging

logger = logging.getLogger("capgainsiq.momentum")


def run_momentum_pipeline(
    config:            dict,
    assets:            list[dict],
    fred_api_key:      str = "",
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Tool 1: Full momentum strategy pipeline.
    Returns live signals, backtest results, allocation history.

    NOT YET IMPLEMENTED.
    See tools/momentum.py for instructions on how to add dad's code here.
    """
    raise NotImplementedError(
        "Tool 1 (Momentum Strategy) is not yet implemented. "
        "See tools/momentum.py for instructions."
    )


def get_live_signal(
    config:            dict,
    assets:            list[dict],
    fred_api_key:      str = "",
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Tool 1: Get current month's buy/sell signals only (quick mode).
    Equivalent to QUICK_DASHBOARD_ONLY=TRUE in original CONFIG.

    NOT YET IMPLEMENTED.
    """
    raise NotImplementedError(
        "Tool 1 (Live Signal) is not yet implemented. "
        "See tools/momentum.py for instructions."
    )

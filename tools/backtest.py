"""
tools/backtest.py
─────────────────────────────────────────────────────────────────────────────
Tool 2 — Portfolio Backtest + XIRR
Source: momentum_strategy_v3.2.1.py (second cell — BACKTEST XIRR)

STATUS: Placeholder — not yet implemented.

HOW TO IMPLEMENT:
  1. Copy the 'BACKTEST XIRR AND CAPITAL GAIN CALCULATION' cell
     from the Colab notebook into this file.
  2. Strip these Colab-specific parts:
       - from google.colab import auth, userdata
       - auth.authenticate_user()
       - gc = gspread.authorize(creds)
       - spreadsheet = gc.open_by_url(...)
       - price_data = wb.worksheet('PRICE_CACHE').get_all_values()
       - hist_data  = wb.worksheet('ALLOCATION_HISTORY').get_all_values()
       - cg_sheet.update(...) calls
  3. Replace sheet reads with function parameters (price_df, hist_df).
  4. Replace sheet writes with return value.
  5. The function signature should be:
       run_backtest(price_df, allocation_history_df, initial_inv=100) -> dict

FUNCTIONS FROM ORIGINAL TO KEEP:
  - safe_xirr()           → already in core/utils.py, import from there
  - get_fy()              → already in core/utils.py
  - compute_tax()         → calc_indian_tax() in core/utils.py
  - run_portfolio_backtest() → adapt this as the main function
─────────────────────────────────────────────────────────────────────────────
"""

from typing import Optional, Callable
import logging

logger = logging.getLogger("capgainsiq.backtest")


def run_backtest(
    price_data:       list[list],   # rows from PRICE_CACHE tab
    allocation_data:  list[list],   # rows from ALLOCATION_HISTORY tab
    initial_inv:      float = 100.0,
    fee_rate:         float = 0.0015,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> dict:
    """
    Tool 2: Portfolio Backtest with post-tax XIRR.

    NOT YET IMPLEMENTED.
    Paste dad's run_portfolio_backtest() logic here when building Tool 2.
    """
    raise NotImplementedError(
        "Tool 2 (Backtest) is not yet implemented. "
        "See tools/backtest.py for instructions."
    )

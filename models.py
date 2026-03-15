"""
models.py
All Pydantic request/response models for all 3 tools.
Frontend TypeScript types in src/types/index.ts mirror these exactly.
"""

from pydantic import BaseModel, field_validator
from typing import Optional, Literal
from enum import Enum


# ─── Shared enums ─────────────────────────────────────────────────────────────

class AssetClass(str, Enum):
    EQUITY    = "EQUITY"
    DEBT      = "DEBT"
    COMMODITY = "COMMODITY"
    MF        = "MF"

class AssetSource(str, Enum):
    YF = "YF"
    MF = "MF"

class TxType(str, Enum):
    BUY  = "Buy"
    SELL = "Sell"


# ─── Tool 3 — Capital Gains ───────────────────────────────────────────────────

class Asset(BaseModel):
    id:          str
    asset_name:  str
    asset_class: AssetClass = AssetClass.EQUITY
    ticker:      str
    source:      AssetSource = AssetSource.YF

    @field_validator("asset_name")
    @classmethod
    def upper_name(cls, v: str) -> str:
        return v.strip().upper()


class Transaction(BaseModel):
    id:               str
    asset_name:       str
    date:             str        # DD-MM-YYYY
    tr_type:          TxType
    rate:             float
    quantity:         float
    amount:           float = 0
    brokerage:        float = 0
    gst:              float = 0
    stt:              float = 0
    sebi_tax:         float = 0
    exchange_charges: float = 0
    stamp_duty:       float = 0
    other_charges:    float = 0
    ipft_charges:     float = 0
    total_charges:    float = 0

    @field_validator("asset_name")
    @classmethod
    def upper_name(cls, v: str) -> str:
        return v.strip().upper()


class FYConfig(BaseModel):
    financial_year:      str
    equity_threshold:    int = 365
    debt_threshold:      int = 1095
    commodity_threshold: int = 1095


class RunCapGainsPayload(BaseModel):
    assets:       list[Asset]
    transactions: list[Transaction]
    config:       list[FYConfig] = []


class CapitalGainRow(BaseModel):
    asset_name:              str
    asset_class:             Optional[str] = None
    ticker:                  Optional[str] = None
    latest_price:            float = 0.0
    remaining_units:         float = 0.0
    current_portfolio_value: float = 0.0
    intraday_cg:             float = 0.0
    r_ltcg:                  float = 0.0
    r_stcg:                  float = 0.0
    r_total:                 float = 0.0
    u_ltcg:                  float = 0.0
    u_stcg:                  float = 0.0
    u_total:                 float = 0.0
    xirr:                    float = 0.0
    total_charges:           float = 0.0
    is_subtotal:             bool  = False
    is_grand_total:          bool  = False


class FYCapitalGain(BaseModel):
    financial_year: str
    intraday_cg:    float = 0.0
    r_stcg:         float = 0.0
    r_ltcg:         float = 0.0
    total_cg:       float = 0.0


class PortfolioSummary(BaseModel):
    total_invested:       float = 0.0
    current_value:        float = 0.0
    total_realised_pnl:   float = 0.0
    total_unrealised_pnl: float = 0.0
    overall_xirr:         float = 0.0
    total_charges:        float = 0.0
    r_ltcg:               float = 0.0
    r_stcg:               float = 0.0
    r_intraday:           float = 0.0
    u_ltcg:               float = 0.0
    u_stcg:               float = 0.0


class CapGainsResult(BaseModel):
    summary:       PortfolioSummary
    capital_gains: list[CapitalGainRow]
    fy_breakdown:  list[FYCapitalGain]
    warnings:      list[str]
    computed_at:   str


# ─── Tool 2 — Backtest (models ready, logic TBD) ──────────────────────────────

class RunBacktestPayload(BaseModel):
    price_data:       list[list]   # raw rows from PRICE_CACHE
    allocation_data:  list[list]   # raw rows from ALLOCATION_HISTORY
    initial_inv:      float = 100.0
    fee_rate:         float = 0.0015


class BacktestResult(BaseModel):
    pre_tax_xirr:   float
    post_tax_xirr:  float
    final_value:    float
    total_tax_paid: float
    cf_log:         list[dict]
    yearly_tax_log: list[dict]
    computed_at:    str


# ─── Tool 1 — Momentum Strategy (models ready, logic TBD) ─────────────────────

class MomentumConfig(BaseModel):
    backtest_start_date:       str   = "2014-06-01"
    top_n_picks:               int   = 7
    transaction_cost:          float = 0.003
    target_vol:                float = 0.0
    safe_asset:                str   = "SBI_LIQUID_MF"
    min_var_max_weight:        float = 0.2
    momentum_lookbacks:        str   = "3,6,9,12"
    regime_filter_asset:       str   = "NIFTY500"
    regime_filter_ma:          int   = 120
    macro_filter_asset:        str   = "US_Y_C_T10Y2Y"
    macro_filter_type:         str   = "LEVEL"
    macro_level_threshold:     float = -0.3
    confirmation_months:       int   = 1
    momentum_smoothing_window: int   = 2
    trailing_stop_pct:         float = 0.12
    cash_regime_risky_cap:     float = 0.42
    fred_api_key:              str   = ""


class MomentumAsset(BaseModel):
    asset_name: str
    ticker:     str
    source:     AssetSource
    type:       str   = "INVEST"   # INVEST / BENCH
    backtest:   bool  = True
    live:       bool  = True


class RunMomentumPayload(BaseModel):
    config: MomentumConfig
    assets: list[MomentumAsset]


class AssetSignal(BaseModel):
    asset:            str
    role:             str
    avg_rank:         Optional[float] = None
    score:            Optional[float] = None
    conf_pct:         Optional[float] = None
    signal:           str
    proposed_alloc_pct: float


class MomentumResult(BaseModel):
    regime:                    str
    signal_date:               str
    consecutive_invest_months: int
    effective_period:          str
    signals:                   list[AssetSignal]
    rolling_returns:           list[dict]
    computed_at:               str


# ─── Shared job response ──────────────────────────────────────────────────────

class JobSubmitResponse(BaseModel):
    job_id: str


class JobStatusResponse(BaseModel):
    status:   Literal["running", "done", "error"]
    progress: Optional[str]         = None
    error:    Optional[str]         = None
    result:   Optional[dict]        = None  # CapGainsResult | BacktestResult | MomentumResult


class PriceResponse(BaseModel):
    ticker: str
    price:  float
    source: str

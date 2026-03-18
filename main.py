"""
main.py
─────────────────────────────────────────────────────────────────────────────
MomentumIQ — FastAPI Backend
Single entry point for all 3 tools.

Routes:
  /api/health                     → health check
  /api/price                      → fetch single price

  Tool 3 — Capital Gains:
  POST /api/capgains/run          → submit job
  GET  /api/capgains/job/:id      → poll status
  GET  /api/capgains/export/:id   → download CSV

  Tool 2 — Backtest (coming soon):
  POST /api/backtest/run
  GET  /api/backtest/job/:id

  Tool 1 — Momentum (coming soon):
  POST /api/momentum/run
  GET  /api/momentum/job/:id
─────────────────────────────────────────────────────────────────────────────
"""

import io
import csv
import logging
from datetime import datetime
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from models import (
    RunCapGainsPayload,
    JobSubmitResponse,
    JobStatusResponse,
    PriceResponse,
)
from tools.capgains  import run_capital_gains
from tools.backtest  import run_backtest
from tools.momentum  import run_momentum_pipeline
from core.utils      import fetch_latest_price
from core.mf_search  import search_funds, get_fund_by_code, auto_match_fund, warmup as mf_warmup
from core.nse_search import search_stocks, auto_match_stock, bulk_match_stocks, warmup as nse_warmup
from jobs.store      import (
    create_job, set_progress, set_done,
    set_error, get_job, delete_job, list_all,
)

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("capgainsiq.api")


# ─── App ──────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("MomentumIQ API starting...")
    mf_warmup()   # pre-load MF fund list in background
    nse_warmup()  # pre-load NSE stock list in background
    yield
    logger.info("MomentumIQ API shutting down.")


app = FastAPI(
    title="MomentumIQ API",
    description=(
        "Backend for MomentumIQ — "
        "Capital Gains (Tool 3), Backtest (Tool 2), Momentum Strategy (Tool 1)"
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",   # Vite dev
        "http://localhost:3000",   # Create React App dev
        "https://*.vercel.app",    # Vercel production
        "*",                       # tighten in production
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Background runners ───────────────────────────────────────────────────────

def _run_capgains_job(job_id: str, payload: RunCapGainsPayload):
    """Run Tool 3 in background thread."""
    try:
        def progress(msg: str):
            set_progress(job_id, msg)

        result = run_capital_gains(
            assets_input       = [a.model_dump(mode="json") for a in payload.assets],
            transactions_input = [t.model_dump(mode="json") for t in payload.transactions],
            config_input       = [c.model_dump(mode="json") for c in payload.config],
            progress_callback  = progress,
        )
        set_done(job_id, result)

    except Exception as e:
        logger.exception(f"CapGains job {job_id[:8]} failed")
        set_error(job_id, str(e))


# ─── Shared routes ────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return {
        "status":  "ok",
        "version": "1.0.0",
        "tools":   ["capgains", "backtest (soon)", "momentum (soon)"],
        "time":    datetime.now().isoformat(),
    }


@app.get("/api/price", response_model=PriceResponse)
def get_price(ticker: str, source: str = "YF"):
    """Fetch latest price for a single ticker."""
    if not ticker:
        raise HTTPException(status_code=422, detail="ticker is required")
    price = fetch_latest_price(ticker, source)
    return {"ticker": ticker, "price": price, "source": source}


@app.get("/api/jobs")
def list_jobs():
    """Debug — list all jobs in memory."""
    return list_all()


# ─── NSE Stock Search ─────────────────────────────────────────────────────────

@app.get("/api/nse/search")
def nse_search(q: str, top_n: int = 5):
    """
    Search NSE stocks by company name or symbol.

    Query params:
      q      — company name or symbol (e.g. "adani enterprises" or "ADANIENT")
      top_n  — max results (default 5, max 10)

    Response:
      [{ symbol, company_name, nse_ticker, bse_ticker, score }]

    Used by frontend AssetsManager for stock ticker typeahead.
    """
    if not q or not q.strip():
        raise HTTPException(status_code=422, detail="q param is required")
    top_n = min(max(1, top_n), 10)
    results = search_stocks(q.strip(), top_n=top_n)
    return {"query": q, "results": results}


@app.post("/api/nse/match-bulk")
def nse_bulk_match(body: dict):
    """
    Bulk auto-match company names to NSE tickers.
    Used during Zerodha CSV import to fix full legal names → short NSE symbols.

    Request body:
      { "names": ["ADANI ENTERPRISES LIMITED", "TATA STEEL LIMITED", ...],
        "min_score": 0.50 }

    Response:
      { "results": {
          "ADANI ENTERPRISES LIMITED": { matched, symbol, nse_ticker, score },
          ...
        }
      }
    """
    names     = body.get("names", [])
    min_score = float(body.get("min_score", 0.50))

    if not names:
        raise HTTPException(status_code=422, detail="names list is required")
    if len(names) > 100:
        raise HTTPException(status_code=422, detail="max 100 names per request")

    results = bulk_match_stocks(names, min_score=min_score)
    return {"results": results}


# ─── MF Search ────────────────────────────────────────────────────────────────

@app.get("/api/mf/search")
def mf_search(q: str, top_n: int = 5):
    """
    Search mutual funds by name. Returns top N matches with confidence scores.

    Query params:
      q      — fund name to search (e.g. "hdfc nifty 50")
      top_n  — max results to return (default 5, max 10)

    Response:
      [{ scheme_code, scheme_name, score }]

    Used by frontend AssetsManager and ZerodhaImporter for auto-suggest.
    """
    if not q or not q.strip():
        raise HTTPException(status_code=422, detail="q param is required")
    top_n = min(max(1, top_n), 10)
    results = search_funds(q.strip(), top_n=top_n)
    return {"query": q, "results": results}


@app.get("/api/mf/match")
def mf_auto_match(name: str, min_score: float = 0.55):
    """
    Auto-match a fund name to its AMFI scheme code.
    Returns best match only if confidence >= min_score.

    Used during Zerodha CSV import to auto-fill MF tickers silently.

    Response:
      { matched: true,  scheme_code, scheme_name, score }
      { matched: false }
    """
    if not name or not name.strip():
        raise HTTPException(status_code=422, detail="name param is required")
    match = auto_match_fund(name.strip(), min_score=min_score)
    if match:
        return {"matched": True, **match}
    return {"matched": False}


@app.post("/api/mf/match-bulk")
def mf_bulk_match(body: dict):
    """
    Auto-match multiple MF names in one request.
    Avoids N separate API calls during CSV import.

    Request body:
      { "names": ["HDFC NIFTY 50 FUND", "ICICI GILT FUND", ...],
        "min_score": 0.55 }

    Response:
      { "results": { "HDFC NIFTY 50 FUND": { matched, scheme_code, scheme_name, score } } }
    """
    names     = body.get("names", [])
    min_score = float(body.get("min_score", 0.55))

    if not names:
        raise HTTPException(status_code=422, detail="names list is required")
    if len(names) > 50:
        raise HTTPException(status_code=422, detail="max 50 names per request")

    results = {}
    for name in names:
        if not name or not str(name).strip():
            continue
        match = auto_match_fund(str(name).strip(), min_score=min_score)
        if match:
            results[name] = {"matched": True, **match}
        else:
            results[name] = {"matched": False}

    return {"results": results}


@app.get("/api/mf/code/{scheme_code}")
def mf_by_code(scheme_code: str):
    """
    Look up fund details by AMFI scheme code.
    Used to validate a code the user has entered manually.
    """
    fund = get_fund_by_code(scheme_code)
    if not fund:
        raise HTTPException(
            status_code=404,
            detail=f"Scheme code '{scheme_code}' not found."
        )
    return fund


# ─── Tool 3: Capital Gains ────────────────────────────────────────────────────

@app.post("/api/capgains/run", response_model=JobSubmitResponse)
def submit_capgains(
    payload: RunCapGainsPayload,
    background_tasks: BackgroundTasks,
):
    """Submit a capital gains calculation. Returns job_id immediately."""
    if not payload.assets:
        raise HTTPException(status_code=422, detail="No assets provided.")
    if not payload.transactions:
        raise HTTPException(status_code=422, detail="No transactions provided.")

    job_id = create_job()
    background_tasks.add_task(_run_capgains_job, job_id, payload)

    logger.info(
        f"CapGains job {job_id[:8]} submitted — "
        f"{len(payload.assets)} assets, "
        f"{len(payload.transactions)} transactions"
    )
    return {"job_id": job_id}


@app.get("/api/capgains/job/{job_id}", response_model=JobStatusResponse)
def poll_capgains(job_id: str):
    """Poll capital gains job status."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return {
        "status":   job["status"],
        "progress": job.get("progress"),
        "error":    job.get("error"),
        "result":   job.get("result"),
    }


@app.get("/api/capgains/export/{job_id}")
def export_capgains_csv(job_id: str, sheet: str = "capital_gains"):
    """Export capital gains result as CSV."""
    job = get_job(job_id)
    if not job or job["status"] != "done" or not job.get("result"):
        raise HTTPException(
            status_code=404,
            detail="Job not found or not complete."
        )

    result   = job["result"]
    output   = io.StringIO()
    writer   = csv.writer(output)

    if sheet == "fy_breakdown":
        writer.writerow([
            "Financial Year", "Intraday CG",
            "R-STCG", "R-LTCG", "Total CG"
        ])
        for row in result["fy_breakdown"]:
            writer.writerow([
                row["financial_year"], row["intraday_cg"],
                row["r_stcg"], row["r_ltcg"], row["total_cg"],
            ])
        filename = "fy_breakdown.csv"
    else:
        writer.writerow([
            "Asset", "Class", "Ticker",
            "Latest Price", "Remaining Units", "Portfolio Value",
            "Intraday CG", "R-LTCG", "R-STCG", "R-Total",
            "U-LTCG", "U-STCG", "U-Total", "XIRR%", "Total Charges",
        ])
        for row in result["capital_gains"]:
            writer.writerow([
                row["asset_name"],
                row.get("asset_class", ""),
                row.get("ticker", ""),
                row["latest_price"],
                row["remaining_units"],
                row["current_portfolio_value"],
                row["intraday_cg"],
                row["r_ltcg"], row["r_stcg"], row["r_total"],
                row["u_ltcg"], row["u_stcg"], row["u_total"],
                row["xirr"],
                row["total_charges"],
            ])
        filename = "capital_gains.csv"

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.delete("/api/capgains/job/{job_id}")
def delete_capgains_job(job_id: str):
    """Clean up completed job from memory."""
    if delete_job(job_id):
        return {"deleted": True}
    raise HTTPException(status_code=404, detail="Job not found.")


# ─── Tool 2: Backtest (routes ready, logic pending) ──────────────────────────

@app.post("/api/backtest/run")
def submit_backtest():
    """
    Tool 2 — Backtest.
    NOT YET IMPLEMENTED. See tools/backtest.py.
    """
    raise HTTPException(
        status_code=501,
        detail="Tool 2 (Backtest) is coming soon. See tools/backtest.py."
    )


@app.get("/api/backtest/job/{job_id}")
def poll_backtest(job_id: str):
    """Tool 2 — Backtest polling. NOT YET IMPLEMENTED."""
    raise HTTPException(status_code=501, detail="Tool 2 coming soon.")


# ─── Tool 1: Momentum Strategy (routes ready, logic pending) ─────────────────

@app.post("/api/momentum/run")
def submit_momentum():
    """
    Tool 1 — Momentum Strategy.
    NOT YET IMPLEMENTED. See tools/momentum.py.
    """
    raise HTTPException(
        status_code=501,
        detail="Tool 1 (Momentum Strategy) is coming soon. See tools/momentum.py."
    )


@app.get("/api/momentum/job/{job_id}")
def poll_momentum(job_id: str):
    """Tool 1 — Momentum polling. NOT YET IMPLEMENTED."""
    raise HTTPException(status_code=501, detail="Tool 1 coming soon.")

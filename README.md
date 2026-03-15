# MomentumIQ — Backend API

## Folder structure

```
backend/
  main.py              ← FastAPI routes (one entry point for all 3 tools)
  models.py            ← All Pydantic models for all 3 tools
  requirements.txt     ← All Python dependencies
  render.yaml          ← Free deployment on Render.com

  core/
    utils.py           ← Shared helpers: price fetch, date parse, XIRR, tax calc

  tools/
    capgains.py        ← Tool 3: Capital Gains (LIVE — dad's exact code)
    backtest.py        ← Tool 2: Backtest + XIRR (placeholder)
    momentum.py        ← Tool 1: Momentum Strategy (placeholder)

  jobs/
    store.py           ← In-memory job queue (swap to Redis when scaling)
```

---

## Run locally

```bash
python -m venv venv
source venv/bin/activate      # Mac/Linux
venv\Scripts\activate         # Windows

pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Open http://localhost:8000/docs for interactive API docs.

Set frontend `.env`:
```
VITE_API_URL=http://localhost:8000
```

---

## Deploy free on Render.com

1. Push backend/ folder to GitHub
2. render.com → New Web Service → connect repo
3. Build: `pip install -r requirements.txt`
4. Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Get URL → update frontend VITE_API_URL

---

## HOW TO UPDATE when dad changes the Colab code

### Tool 3 (Capital Gains) — tools/capgains.py

When dad updates the capital gains cell in Colab:

1. Open `tools/capgains.py`
2. Find the section marked `# ── FIFO Logic ──`
3. Replace with dad's new logic
4. Only strip these Colab lines:
   ```python
   # REMOVE:
   from google.colab import auth, userdata
   auth.authenticate_user()
   gc = gspread.authorize(creds)
   spreadsheet = gc.open_by_url(...)
   ws.update(...) / write_sheet(...)
   ```
5. Keep ALL calculation logic exactly as-is
6. Restart the server — done

### Tool 2 (Backtest) — tools/backtest.py

When ready to build Tool 2:
1. Open tools/backtest.py — instructions are at the top
2. Copy the backtest cell from Colab
3. Strip the Colab/gspread parts
4. Replace `raise NotImplementedError` with the real logic
5. Update main.py `/api/backtest/run` to call it

### Tool 1 (Momentum) — tools/momentum.py

Same process. Instructions at top of tools/momentum.py.

---

## API endpoints

| Method | URL | Tool | Status |
|--------|-----|------|--------|
| GET  | /api/health | — | ✅ Live |
| GET  | /api/price | — | ✅ Live |
| POST | /api/capgains/run | Tool 3 | ✅ Live |
| GET  | /api/capgains/job/:id | Tool 3 | ✅ Live |
| GET  | /api/capgains/export/:id | Tool 3 | ✅ Live |
| POST | /api/backtest/run | Tool 2 | 🔜 Soon |
| POST | /api/momentum/run | Tool 1 | 🔜 Soon |

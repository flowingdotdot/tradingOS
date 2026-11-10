# Trading OS

Research, backtesting and (later) automated execution platform for OKX — plus a separate macro
investment-research track. **LIVE trading is hard-disabled.** Nothing here is a profitable strategy.

- Docs: [Product](docs/PRODUCT_SPEC.md) · [Architecture](docs/ARCHITECTURE.md) · [Strategy](docs/STRATEGY_SPEC.md) ·
  [Risk](docs/RISK_POLICY.md) · [Roadmap](docs/ROADMAP.md) · [Investment Intelligence](docs/INVESTMENT_INTELLIGENCE_SPEC.md)

## Status — Milestone 1
OKX historical candles → Strategy (educational EMA crossover) → Backtest → Results UI.

## Setup (Windows, PowerShell or Git Bash)

Requires Python 3.12 (installed via `uv`) and Node 20+.

```bash
# backend
python -m pip install --user uv
cd backend
python -m uv python install 3.12
python -m uv venv --python 3.12 .venv
python -m uv pip install --python .venv/Scripts/python.exe -e ".[dev]"
# behind antivirus/corporate HTTPS inspection add:  --native-tls   (or set UV_NATIVE_TLS=1)

# frontend
cd ../frontend
npm install          # if TLS errors: set NODE_OPTIONS=--use-system-ca
```

## Run

```bash
# 1) collect data (public OKX endpoint, no API key)
backend/.venv/Scripts/tradingos collect --tf 15m --days 180

# 2) optional: backtest from the CLI
backend/.venv/Scripts/tradingos backtest --strategy ema_cross --version v1 --tf 15m --days 150

# 3) API (port 8000) + UI (port 5173)
backend/.venv/Scripts/tradingos serve
cd frontend && npm run dev          # open http://localhost:5173
```

## Tests

```bash
cd backend
.venv/Scripts/python -m pytest              # unit + integration (mock OKX)
.venv/Scripts/python -m pytest -m network   # smoke tests against the real OKX public API
cd ../frontend && npm run build             # typecheck + production build
```

## Layout

```
docs/       specifications
backend/    FastAPI + engines (src/tradingos), strategy YAML (config/strategies), tests
frontend/   React + TypeScript + Vite + lightweight-charts
data/       runtime data — Parquet candles, SQLite runs (git-ignored)
```

# VANTA

Research platform for studying how macroeconomic information impacts FX markets and for developing systematic event-driven strategies.

## Repo layout
- `data/raw/` holds *unmodified* source downloads (ignored by git by default).
- `data/processed/` holds derived/working datasets.
- `database/schema.sql` defines the SQLite schema.
- `helper scripts/` contains small command-line utilities for Phase 1/2.

## Phase 1 (calendar inspection)

Raw input (example):
- `data/raw/economic_calendar/forex_factory_cache.csv`

Run the QC/inspection script (stdlib-only):
- `& "C:\Users\ikema\AppData\Local\Programs\Python\Python310\python.exe" "helper scripts\inspect_calendar.py"`

Outputs:
- `data/raw/economic_calendar/source_manifest.json`
- `data/processed/economic_events/calendar_qc_report.json`
- `data/processed/economic_events/economic_calendar_2020_high_impact.csv`

## Phase 2 (SQLite database + import)

Import the Phase-1 working extract into SQLite (defaults exclude `CNY` and `All`):
- `& "C:\Users\ikema\AppData\Local\Programs\Python\Python310\python.exe" "helper scripts\import_calendar.py"`

Database:
- `data/vanta.sqlite` (ignored by git)

Sanity-check the DB:
- `& "C:\Users\ikema\AppData\Local\Programs\Python\Python310\python.exe" "helper scripts\query_db.py" --db data/vanta.sqlite`

## Phase 3 (FX 1-minute candles)

We use Dukascopy's public historical data feed (route 1) for bid+ask 1-minute candles.

Example: download + decode a small test range for EURUSD:
- `& "C:\Users\ikema\AppData\Local\Programs\Python\Python310\python.exe" "helper scripts\download_prices.py" --symbols EURUSD --start 2020-01-01 --end 2020-01-07`

Batch runner (recommended for multi-year downloads):
- `& "C:\Users\ikema\AppData\Local\Programs\Python\Python310\python.exe" "helper scripts\run_download_batches.py" --start 2020-01-01 --end 2020-12-31 --progress`

By default the batch runner downloads **by symbol** (priority order) so you can start research with the most important pairs sooner. Use `--mode by-month` for the legacy month-first approach.

If your terminal doesn't render ANSI updates well, use snapshot mode:
- `& "C:\Users\ikema\AppData\Local\Programs\Python\Python310\python.exe" "helper scripts\run_download_batches.py" --start 2020-01-01 --end 2020-12-31 --progress --no-ansi`

Outputs:
- Raw `.bi5` downloads: `data/raw/price_data/dukascopy/...`
- Processed daily bid+ask CSVs: `data/processed/price_data/dukascopy_m1/...`

QC (optional):
- `& "C:\Users\ikema\AppData\Local\Programs\Python\Python310\python.exe" "helper scripts\clean_prices.py"`

## Phase 4 (research dataset)

Build an eventâ†”price reaction dataset from the SQLite calendar + downloaded candles:
- `& "C:\Users\ikema\AppData\Local\Programs\Python\Python310\python.exe" "helper scripts\build_event_dataset.py" --symbols EURUSD USDJPY GBPUSD AUDUSD USDCAD --events-start 2020-01-01 --events-end 2020-12-31`

Validate missing-rate stats:
- `& "C:\Users\ikema\AppData\Local\Programs\Python\Python310\python.exe" "helper scripts\validate_dataset.py"`

## Tick impulse analysis (experimental)

Measure the first-direction post-event impulse using Dukascopy tick data (daily `.bi5`).
This is intended to help tune Strategy C parameters (distance/expiry) based on typical one-way moves.

Example (start small):
- `& "C:\Users\ikema\AppData\Local\Programs\Python\Python310\python.exe" "helper scripts\analyze_event_impulse_ticks.py" --symbols EURUSD --events-start 2020-01-01 --events-end 2020-12-31 --max-rows 100`

Outputs:
- `data/processed/research/impulse_ticks_per_event.csv`
- `data/processed/research/impulse_ticks_summary.json`

Notes:
- Downloads tick files into `data/raw/price_data/dukascopy_ticks/` (ignored by git).

## Live / demo (MT5)

Create a `.env` (ignored by git) based on `.env.example` and set:
- `MT5_TERMINAL_PATH`
- `MT5_LOGIN`
- `MT5_PASSWORD`
- `MT5_SERVER`

Test connectivity:
- `& "C:\Users\ikema\AppData\Local\Programs\Python\Python310\python.exe" "strategies\connection_test.py"`

MT5 smoke test (no orders):
- `& "C:\Users\ikema\AppData\Local\Programs\Python\Python310\python.exe" "strategies\mt5_smoke_test.py" --symbol EURUSD`

Order lifecycle test (dry-run by default; requires `--arm` to actually place/cancel):
- `& "C:\Users\ikema\AppData\Local\Programs\Python\Python310\python.exe" "strategies\order_lifecycle_test.py" --symbol EURUSD`
- `& "C:\Users\ikema\AppData\Local\Programs\Python\Python310\python.exe" "strategies\order_lifecycle_test.py" --symbol EURUSD --arm`

Strategy C (simple straddle around upcoming High-impact Forex Factory events):
- `& "C:\Users\ikema\AppData\Local\Programs\Python\Python310\python.exe" "strategies\strategy_c_news_volatility.py" --symbols EURUSD`

Risk-based sizing example (risk 0.25% per trade, SL 20 pips):
- `& "C:\Users\ikema\AppData\Local\Programs\Python\Python310\python.exe" "strategies\strategy_c_news_volatility.py" --symbols EURUSD --risk-pct 0.25 --sl-pips 20 --risk-base balance`


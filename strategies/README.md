# Strategies

This folder contains live/demo strategy runners (MT5) and supporting utilities.

## Setup

Create a `.env` in the repo root (or any parent folder you run from) based on `.env.example`:
- `MT5_TERMINAL_PATH` (path to `terminal64.exe`)
- `MT5_LOGIN`
- `MT5_PASSWORD`
- `MT5_SERVER`

The scripts auto-load `.env` by searching upward from the current directory.

## Quick tests

### 1) Connection test
Logs into MT5 and prints account info.

- `py strategies/connection_test.py`

### 2) Smoke test (no orders)
Verifies tick access, recent range calculation, and risk-based lot sizing.

- `py strategies/mt5_smoke_test.py --symbol EURUSD --risk-pct 0.25 --sl-pips 20 --risk-base balance`

### 3) Order lifecycle test (place + cancel)
Prepares a buy-stop and sell-stop far away from price. Default is **dry-run**.

- Dry run: `py strategies/order_lifecycle_test.py --symbol EURUSD`
- Armed: `py strategies/order_lifecycle_test.py --symbol EURUSD --arm`

## Strategy C (News-volatility straddle)

File: `strategies/strategy_c_news_volatility.py`

### What it does
- Pulls this week's schedule from Forex Factory's public weekly JSON export.
- Filters to upcoming events in the next `--window-hours` (default 24h).
- Filters by impact via `--impact` (default `High`).
- For each event, places **two pending orders** on your chosen MT5 symbol(s):
  - Buy Stop above price
  - Sell Stop below price
- Orders are placed `--lead-seconds` before the scheduled event time (default 90s).

### Key parameters
- `--symbols` Symbols to trade (e.g. `EURUSD`).
- `--currencies` Optional override for which event currencies to trade. If omitted, currencies are derived from `--symbols` (e.g. `EURUSD` -> EUR + USD).
- `--window-hours` How far ahead to schedule events.
- `--impact` Impact filter (default `High`).
- `--lead-seconds` When to place orders before the event.
- `--lookback-minutes` Range lookback window used to size the entry distance.
- `--distance-mult` Entry distance multiplier (distance is based on recent range).
- `--min-distance-pips` Minimum pending distance.
- `--sl-pips` Stop-loss distance in pips.
- `--tp-pips` Take-profit distance in pips.

### Risk sizing
You can use either:
- Fixed lot size: `--lot 0.01`
- Risk-based sizing: `--risk-pct 0.25 --risk-base balance` (recommended)

Risk-based sizing computes lot size from your account balance/equity and `--sl-pips`.

### Example run

- `py strategies/strategy_c_news_volatility.py --symbols EURUSD --risk-pct 0.25 --sl-pips 20 --risk-base balance`

### Important limitations (current version)
- This runner places the straddle but does not yet manage the lifecycle (e.g. cancel the opposite order after one triggers, time-based cleanup, etc.).
- It uses Forex Factory's weekly export for schedule/impact/forecast/previous, but it does **not** provide released actual values.


### Entry testing (recommended before live news)
Run a safe test that places a straddle soon and then cancels it.

- Dry run (no orders sent):
  - py strategies/strategy_c_news_volatility.py --symbols EURUSD --risk-pct 0.25 --sl-pips 20 --risk-base balance --test-entry --dry-run
- Armed (places pending orders, waits ~15s, then cancels):
  - py strategies/strategy_c_news_volatility.py --symbols EURUSD --risk-pct 0.25 --sl-pips 20 --risk-base balance --test-entry --test-hold-seconds 15


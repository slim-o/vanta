"""MT5 smoke test: login, tick, recent range, sizing.

Purpose: verify MT5 plumbing without placing any trades.
"""

from __future__ import annotations

import argparse
import os

from mt5_helper.dotenv import load_dotenv
from mt5_helper.general_functions import (
    calc_lot_from_risk,
    ensure_symbol,
    get_bid_ask,
    recent_range_pips,
    retryable_initialize,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="MT5 smoke test")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--risk-pct", type=float, default=0.25)
    parser.add_argument("--sl-pips", type=float, default=20.0)
    parser.add_argument("--risk-base", choices=["balance", "equity"], default="balance")
    parser.add_argument("--lookback-minutes", type=int, default=60)
    args = parser.parse_args()

    loaded = load_dotenv()
    if loaded:
        print(f"Loaded .env: {loaded}")

    terminal_path = os.environ.get("MT5_TERMINAL_PATH", "").strip()
    if not terminal_path:
        raise SystemExit("Set MT5_TERMINAL_PATH (and MT5_LOGIN/MT5_PASSWORD/MT5_SERVER).")

    retryable_initialize(max_retries=5, delay_seconds=1, terminal_path=terminal_path)
    symbol = args.symbol.upper()
    ensure_symbol(symbol)
    bid, ask = get_bid_ask(symbol)
    print(f"{symbol} bid={bid} ask={ask}")
    rng = recent_range_pips(symbol, lookback_minutes=int(args.lookback_minutes))
    print(f"{symbol} range last {args.lookback_minutes}m: {rng:.1f} pips")
    lot = calc_lot_from_risk(
        symbol=symbol,
        risk_pct=float(args.risk_pct),
        sl_pips=float(args.sl_pips),
        use_equity=(args.risk_base == "equity"),
    )
    print(f"Sized lot for risk={args.risk_pct}% sl={args.sl_pips}pips base={args.risk_base}: {lot}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


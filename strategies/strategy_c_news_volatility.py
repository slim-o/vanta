"""Strategy C: Bidirectional news-volatility trading (live/paper via MT5).

This is a minimal live runner:
- Pulls this week's schedule from Forex Factory's public weekly JSON export.
- Filters for high-impact events and selected currencies.
- For each event, places a straddle of pending orders (buy-stop & sell-stop)
  a configurable number of seconds before the release.

It is intentionally conservative: it places orders but does not attempt to
manage them after entry (cancel opposite, trail stops, etc.).
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import time
from typing import List, Set

from event_sources.forex_factory_weekly import fetch_thisweek_events, filter_events
from mt5_helper.general_functions import (
    calc_lot_from_risk,
    place_straddle_pending_orders,
    recent_range_pips,
    retryable_initialize,
)
from mt5_helper.dotenv import load_dotenv


DEFAULT_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF"}


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_currencies(values: List[str]) -> Set[str]:
    out: Set[str] = set()
    for v in values:
        for part in v.replace(",", " ").split():
            out.add(part.strip().upper())
    return out


def currencies_from_symbols(symbols: List[str]) -> Set[str]:
    out: Set[str] = set()
    for s in symbols:
        sym = s.upper().strip()
        if len(sym) == 6 and sym.isalpha():
            out.add(sym[:3])
            out.add(sym[3:])
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Live Strategy C runner (MT5 pending-order straddle)")
    parser.add_argument("--symbols", nargs="*", default=["EURUSD"], help="Symbols to trade")
    parser.add_argument(
        "--currencies",
        nargs="*",
        default=[],
        help="Currencies to watch (Forex Factory 'country' field). If omitted, derive from --symbols.",
    )
    parser.add_argument("--impact", default="High", help="Impact filter (default High)")
    parser.add_argument(
        "--lead-seconds",
        type=int,
        default=90,
        help="Place straddle this many seconds before the scheduled event time",
    )
    parser.add_argument(
        "--lookback-minutes",
        type=int,
        default=60,
        help="Lookback window for pre-event range estimate",
    )
    parser.add_argument(
        "--distance-mult",
        type=float,
        default=0.5,
        help="Order distance = max(min-distance, lookback_range_pips * mult)",
    )
    parser.add_argument("--min-distance-pips", type=float, default=5.0)
    parser.add_argument("--sl-pips", type=float, default=20.0)
    parser.add_argument("--tp-pips", type=float, default=30.0)
    parser.add_argument("--lot", type=float, default=0.01, help="Fixed lot size (ignored if --risk-pct > 0)")
    parser.add_argument(
        "--risk-pct",
        type=float,
        default=0.0,
        help="If >0, compute lot size per symbol from risk%% and --sl-pips",
    )
    parser.add_argument(
        "--risk-base",
        choices=["balance", "equity"],
        default="balance",
        help="Use balance or equity as the base for risk sizing (default balance)",
    )
    parser.add_argument("--magic", type=int, default=7003001)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument(
        "--window-hours",
        type=float,
        default=24.0,
        help="Only schedule events in the next N hours",
    )
    args = parser.parse_args()

    loaded = load_dotenv()
    if loaded:
        print(f"Loaded .env: {loaded}")

    terminal_path = os.environ.get("MT5_TERMINAL_PATH", "").strip()
    if not terminal_path:
        raise SystemExit("Set MT5_TERMINAL_PATH (and MT5_LOGIN/MT5_PASSWORD/MT5_SERVER) before running.")

    retryable_initialize(max_retries=5, delay_seconds=1, terminal_path=terminal_path)

    symbols = [s.upper() for s in args.symbols]
    if args.currencies:
        currencies = set(parse_currencies(args.currencies))
    else:
        currencies = currencies_from_symbols(symbols) or set(DEFAULT_CURRENCIES)

    all_events = fetch_thisweek_events()
    start = utc_now()
    end = start + dt.timedelta(hours=float(args.window_hours))
    events = filter_events(
        all_events,
        currencies=currencies,
        impacts={args.impact},
        start_utc=start,
        end_utc=end,
    )

    if not events:
        print("No upcoming events found in window.")
        return 0

    print(f"Upcoming events (UTC) in next {args.window_hours}h: {len(events)}")
    for e in events[:50]:
        print(f"- {e.scheduled_at_utc.isoformat()} {e.country} {e.impact}: {e.title}")

    traded_keys = set()
    while True:
        now = utc_now()
        if now > end:
            print("Window complete.")
            return 0

        for e in events:
            key = (e.scheduled_at_utc.isoformat(), e.country, e.title)
            if key in traded_keys:
                continue

            fire_at = e.scheduled_at_utc - dt.timedelta(seconds=int(args.lead_seconds))
            if now < fire_at:
                continue

            # Place straddles on requested symbols; this is intentionally naive.
            for sym in symbols:
                try:
                    rng = recent_range_pips(sym, lookback_minutes=int(args.lookback_minutes))
                    dist = max(float(args.min_distance_pips), float(rng) * float(args.distance_mult))

                    lot = float(args.lot)
                    if float(args.risk_pct) > 0:
                        lot = calc_lot_from_risk(
                            symbol=sym,
                            risk_pct=float(args.risk_pct),
                            sl_pips=float(args.sl_pips),
                            use_equity=(args.risk_base == "equity"),
                        )

                    buy_ticket, sell_ticket = place_straddle_pending_orders(
                        symbol=sym,
                        lot=lot,
                        distance_pips=float(dist),
                        sl_pips=float(args.sl_pips),
                        tp_pips=float(args.tp_pips),
                        magic=int(args.magic),
                        comment=f"VANTA_StrategyC {e.country} {e.title}",
                    )
                    print(
                        f"Placed {sym} straddle for {e.country} {e.title} at {now.isoformat()} lot={lot} dist={dist:.1f}pips "
                        f"tickets=({buy_ticket},{sell_ticket})"
                    )
                except Exception as ex:  # noqa: BLE001
                    print(f"Failed placing straddle for {sym} {e.country} {e.title}: {ex}")

            traded_keys.add(key)

        time.sleep(float(args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())


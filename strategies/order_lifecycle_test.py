"""Place test pending orders far from market, then cancel them.

Purpose: validate order placement permissions, ticket handling, and cancellation
logic without relying on any calendar/event logic.

Safety:
- Defaults to DRY RUN.
- Requires --arm to actually place orders.
"""

from __future__ import annotations

import argparse
import os
import time

import MetaTrader5 as mt5

from mt5_helper.dotenv import load_dotenv
from mt5_helper.general_functions import (
    cancel_orders_by_magic,
    ensure_symbol,
    get_bid_ask,
    pip_size,
    retryable_initialize,
    round_price,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="MT5 order lifecycle test")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--lot", type=float, default=0.01)
    parser.add_argument("--distance-pips", type=float, default=200.0)
    parser.add_argument("--sl-pips", type=float, default=20.0)
    parser.add_argument("--tp-pips", type=float, default=20.0)
    parser.add_argument("--magic", type=int, default=7099001)
    parser.add_argument("--arm", action="store_true", help="Actually place/cancel orders")
    parser.add_argument("--wait-seconds", type=float, default=2.0)
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
    pip = pip_size(symbol)

    buy_price = round_price(symbol, ask + float(args.distance_pips) * pip)
    sell_price = round_price(symbol, bid - float(args.distance_pips) * pip)

    buy_sl = round_price(symbol, buy_price - float(args.sl_pips) * pip)
    buy_tp = round_price(symbol, buy_price + float(args.tp_pips) * pip)
    sell_sl = round_price(symbol, sell_price + float(args.sl_pips) * pip)
    sell_tp = round_price(symbol, sell_price - float(args.tp_pips) * pip)

    comment = "VANTA_OrderLifecycleTest"

    buy_req = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": symbol,
        "volume": float(args.lot),
        "type": mt5.ORDER_TYPE_BUY_STOP,
        "price": buy_price,
        "sl": buy_sl,
        "tp": buy_tp,
        "deviation": 50,
        "magic": int(args.magic),
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }
    sell_req = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": symbol,
        "volume": float(args.lot),
        "type": mt5.ORDER_TYPE_SELL_STOP,
        "price": sell_price,
        "sl": sell_sl,
        "tp": sell_tp,
        "deviation": 50,
        "magic": int(args.magic),
        "comment": comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }

    print("Prepared buy-stop request:", buy_req)
    print("Prepared sell-stop request:", sell_req)

    if not args.arm:
        print("DRY RUN: pass --arm to actually place/cancel orders")
        return 0

    # Clean up any previous test orders.
    cancelled = cancel_orders_by_magic(magic=int(args.magic), symbol=symbol)
    if cancelled:
        print(f"Cancelled previous test orders: {cancelled}")

    buy_res = mt5.order_send(buy_req)
    if buy_res is None or buy_res.retcode != mt5.TRADE_RETCODE_DONE:
        raise SystemExit(f"Buy-stop placement failed: {buy_res} / {mt5.last_error()}")
    sell_res = mt5.order_send(sell_req)
    if sell_res is None or sell_res.retcode != mt5.TRADE_RETCODE_DONE:
        raise SystemExit(f"Sell-stop placement failed: {sell_res} / {mt5.last_error()}")

    print(f"Placed orders: buy={buy_res.order} sell={sell_res.order}")
    time.sleep(float(args.wait_seconds))

    cancelled = cancel_orders_by_magic(magic=int(args.magic), symbol=symbol)
    print(f"Cancelled orders: {cancelled}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


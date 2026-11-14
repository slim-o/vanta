import os
import re
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import MetaTrader5 as mt5

class MaxRetriesExceeded(Exception):
    pass


def sanitize_mt5_comment(comment: Optional[str], *, max_len: int = 31) -> str:
    """Return a broker-safe MT5 order comment.

    Many brokers enforce a short comment limit (commonly 31 chars) and reject
    non-printable / non-ASCII characters.
    """

    if comment is None:
        return ""
    text = str(comment)
    # Keep printable ASCII only.
    text = "".join(ch for ch in text if 32 <= ord(ch) <= 126)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[:max_len]
    return text

def _env(name: str) -> Optional[str]:
    value = os.environ.get(name, "").strip()
    return value or None


def retryable_initialize(max_retries: int, delay_seconds: int, terminal_path: str) -> bool:
    """Initialise MT5 terminal and login.

    Credentials are read from environment variables:
    - MT5_LOGIN
    - MT5_PASSWORD
    - MT5_SERVER

    This avoids committing secrets to the repo.
    """

    login_raw = _env("MT5_LOGIN")
    password = _env("MT5_PASSWORD")
    server = _env("MT5_SERVER")

    if not login_raw or not password or not server:
        raise RuntimeError(
            "Missing MT5 credentials. Set MT5_LOGIN, MT5_PASSWORD, MT5_SERVER in your environment (or .env)."
        )

    login = int(login_raw)

    for attempt in range(1, max_retries + 1):
        if mt5.initialize(terminal_path):
            
            authorized = mt5.login(login=login, password=password, server=server)
            
            if authorized:
                # display trading account data 'as is'
                print(f"Connected to {mt5.account_info()[0]}")
                
            else:
                print(f"Failed to login for account #{login}, error code: {mt5.last_error()}")
                if datetime.now(timezone.utc).hour == 9:
                    time.sleep(1860)
            return True  # If successful, exit the loop and return True
        else:
            print(f"Attempt {attempt} failed to initialize, error code: {mt5.last_error()}")
            if attempt == max_retries - 1:
                time.sleep(delay_seconds * 3600)  # Wait for the specified time before the next attempt
                attempt = 1

            
    #send_notification('initialisation failed', f'{mt_account} failed to connect')
    raise MaxRetriesExceeded(f"Max retries ({max_retries}) reached. Initialization failed.")


def ensure_symbol(symbol: str) -> None:
    if not mt5.symbol_select(symbol, True):
        raise RuntimeError(f"Failed to select symbol {symbol}: {mt5.last_error()}")


def get_bid_ask(symbol: str) -> Tuple[float, float]:
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"No tick for {symbol}: {mt5.last_error()}")
    return float(tick.bid), float(tick.ask)


def point_and_digits(symbol: str) -> Tuple[float, int]:
    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"No symbol info for {symbol}: {mt5.last_error()}")
    return float(info.point), int(info.digits)


def pip_size(symbol: str) -> float:
    point, digits = point_and_digits(symbol)
    # For most FX pairs pip is 0.0001, for JPY pairs pip is 0.01.
    return 0.01 if digits <= 3 else 0.0001


def round_price(symbol: str, price: float) -> float:
    _, digits = point_and_digits(symbol)
    return round(price, digits)


def recent_range_pips(symbol: str, lookback_minutes: int = 60) -> float:
    """Range (high-low) over the last N minutes, in pips."""
    rates = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M1, 0, int(lookback_minutes))
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"No rates for {symbol}: {mt5.last_error()}")
    highs = [float(r[2]) for r in rates]  # high
    lows = [float(r[3]) for r in rates]  # low
    rng = max(highs) - min(lows)
    return rng / pip_size(symbol)


def place_straddle_pending_orders(
    *,
    symbol: str,
    lot: float,
    distance_pips: float,
    sl_pips: float,
    tp_pips: float,
    magic: int,
    comment: str,
) -> Tuple[int, int]:
    """Place buy-stop and sell-stop around current price.

    Returns (buy_order_ticket, sell_order_ticket).
    """

    ensure_symbol(symbol)
    bid, ask = get_bid_ask(symbol)
    pip = pip_size(symbol)

    buy_price = round_price(symbol, ask + distance_pips * pip)
    sell_price = round_price(symbol, bid - distance_pips * pip)

    buy_sl = round_price(symbol, buy_price - sl_pips * pip)
    buy_tp = round_price(symbol, buy_price + tp_pips * pip)
    sell_sl = round_price(symbol, sell_price + sl_pips * pip)
    sell_tp = round_price(symbol, sell_price - tp_pips * pip)
    safe_comment = sanitize_mt5_comment(comment)

    common = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": symbol,
        "volume": float(lot),
        "deviation": 50,
        "magic": int(magic),
        "comment": safe_comment,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }

    buy_req = {
        **common,
        "type": mt5.ORDER_TYPE_BUY_STOP,
        "price": buy_price,
        "sl": buy_sl,
        "tp": buy_tp,
    }
    sell_req = {
        **common,
        "type": mt5.ORDER_TYPE_SELL_STOP,
        "price": sell_price,
        "sl": sell_sl,
        "tp": sell_tp,
    }

    buy_res = mt5.order_send(buy_req)
    if buy_res is None or buy_res.retcode != mt5.TRADE_RETCODE_DONE:
        raise RuntimeError(f"Buy-stop failed for {symbol}: {buy_res} / {mt5.last_error()}")
    sell_res = mt5.order_send(sell_req)
    if sell_res is None or sell_res.retcode != mt5.TRADE_RETCODE_DONE:
        raise RuntimeError(f"Sell-stop failed for {symbol}: {sell_res} / {mt5.last_error()}")

    return int(buy_res.order), int(sell_res.order)


def pending_orders(*, symbol: Optional[str] = None, magic: Optional[int] = None):
    """Return pending orders filtered by symbol/magic."""
    orders = mt5.orders_get()
    if orders is None:
        return []
    out = []
    for o in orders:
        if symbol and str(getattr(o, "symbol", "")) != symbol:
            continue
        if magic is not None and int(getattr(o, "magic", 0)) != int(magic):
            continue
        out.append(o)
    return out


def cancel_order(ticket: int) -> bool:
    req = {
        "action": mt5.TRADE_ACTION_REMOVE,
        "order": int(ticket),
    }
    res = mt5.order_send(req)
    if res is None:
        raise RuntimeError(f"Cancel order returned None: {mt5.last_error()}")
    return res.retcode == mt5.TRADE_RETCODE_DONE



def open_positions(*, symbol: Optional[str] = None, magic: Optional[int] = None):
    """Return open positions filtered by symbol/magic."""
    positions = mt5.positions_get()
    if positions is None:
        return []
    out = []
    for p in positions:
        if symbol and str(getattr(p, "symbol", "")) != symbol:
            continue
        if magic is not None and int(getattr(p, "magic", 0)) != int(magic):
            continue
        out.append(p)
    return out


def close_position(ticket: int, *, deviation: int = 50) -> bool:
    """Close a position at market by position ticket."""
    pos = None
    for p in open_positions():
        if int(getattr(p, "ticket", 0)) == int(ticket):
            pos = p
            break
    if pos is None:
        return False

    symbol = str(getattr(pos, "symbol", ""))
    volume = float(getattr(pos, "volume", 0.0) or 0.0)
    if not symbol or volume <= 0:
        raise RuntimeError(f"Invalid position for close: {pos}")

    bid, ask = get_bid_ask(symbol)
    pos_type = int(getattr(pos, "type", 0))
    if pos_type == mt5.POSITION_TYPE_BUY:
        order_type = mt5.ORDER_TYPE_SELL
        price = bid
    else:
        order_type = mt5.ORDER_TYPE_BUY
        price = ask

    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "position": int(ticket),
        "volume": float(volume),
        "type": int(order_type),
        "price": float(price),
        "deviation": int(deviation),
        "magic": int(getattr(pos, "magic", 0) or 0),
        "comment": sanitize_mt5_comment("VANTA close"),
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }
    res = mt5.order_send(req)
    if res is None:
        raise RuntimeError(f"Close position returned None: {mt5.last_error()}")
    return res.retcode == mt5.TRADE_RETCODE_DONE


def close_positions_by_magic(*, magic: int, symbol: Optional[str] = None) -> int:
    """Close all open positions matching magic (and optional symbol)."""
    positions = open_positions(symbol=symbol, magic=magic)
    closed = 0
    for p in positions:
        if close_position(int(getattr(p, "ticket", 0))):
            closed += 1
    return closed

def cancel_orders_by_magic(*, magic: int, symbol: Optional[str] = None) -> int:
    """Cancel all pending orders matching magic (and optional symbol)."""
    orders = pending_orders(symbol=symbol, magic=magic)
    cancelled = 0
    for o in orders:
        if cancel_order(int(o.ticket)):
            cancelled += 1
    return cancelled


def calc_lot_from_risk(
    *,
    symbol: str,
    risk_pct: float,
    sl_pips: float,
    use_equity: bool = True,
) -> float:
    """Calculate lot size from risk percentage and SL pips.

    Uses MT5 symbol trade tick value/size to estimate pip value per 1 lot in the
    account deposit currency.
    """

    info = mt5.symbol_info(symbol)
    if info is None:
        raise RuntimeError(f"No symbol info for {symbol}: {mt5.last_error()}")

    acc = mt5.account_info()
    if acc is None:
        raise RuntimeError(f"No account info: {mt5.last_error()}")

    base_amount = float(acc.equity if use_equity else acc.balance)
    risk_amount = base_amount * (float(risk_pct) / 100.0)
    if risk_amount <= 0:
        raise ValueError("risk_pct results in non-positive risk amount")
    if sl_pips <= 0:
        raise ValueError("sl_pips must be > 0")

    tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
    tick_size = float(getattr(info, "trade_tick_size", 0.0) or 0.0)
    if tick_value <= 0 or tick_size <= 0:
        raise RuntimeError(f"Symbol missing tick value/size for sizing: {symbol}")

    pip = pip_size(symbol)
    pip_value_per_lot = tick_value * (pip / tick_size)
    if pip_value_per_lot <= 0:
        raise RuntimeError("Computed non-positive pip value")

    raw_lot = risk_amount / (float(sl_pips) * pip_value_per_lot)

    vol_min = float(getattr(info, "volume_min", 0.01) or 0.01)
    vol_max = float(getattr(info, "volume_max", raw_lot) or raw_lot)
    vol_step = float(getattr(info, "volume_step", 0.01) or 0.01)

    lot = max(vol_min, min(vol_max, raw_lot))
    # Round down to step.
    lot = (lot // vol_step) * vol_step
    # Avoid 0 due to flooring.
    if lot < vol_min:
        lot = vol_min
    return float(lot)









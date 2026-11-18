"""Analyze one-way post-event impulse using Dukascopy tick data.

Goal
- For each (event, symbol), measure the *first-direction impulse* after the event,
  up to a retrace back to `mid0` (mid price at event time) or a time limit.

Definitions (per event)
- `t0`: scheduled event time (UTC)
- `mid0`: mid price at `t0` (last tick at-or-before t0)
- First-direction trigger: the first time mid deviates by >= `trigger_pips`
  from `mid0` (up or down)
- Impulse run: the maximum extension in that first direction, stopping when:
  - mid crosses back through `mid0` (retrace-to-start), OR
  - `t0 + window_minutes` is reached

Why tick data
- For Strategy C / stop orders, bid/ask and spread spikes matter.

Inputs
- Uses `data/processed/research/event_reactions_m1.csv` by default to get a
  list of event timestamps + symbols.

Outputs
- Per-event CSV with impulse distance and timings.
- Summary JSON with medians/percentiles.

Data source
- Dukascopy tick `.bi5` daily files via the public datafeed.
  Decompressed tick record format is 20 bytes big-endian: `>IIIff`.
  (ms_since_day_start, ask_u32, bid_u32, ask_vol_f32, bid_vol_f32)

Reference
- Dukascopy wiki: decoding `.bi5` tick files.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import lzma
import random
import socket
import sqlite3
import struct
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple


DUKASCOPY_BASE_URL = "https://datafeed.dukascopy.com/datafeed"
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}


def month_zero_based(date: dt.date) -> str:
    return f"{date.month - 1:02d}"


def pip_size(symbol: str) -> float:
    s = symbol.upper().strip()
    return 0.01 if s.endswith("JPY") else 0.0001


def point_value(symbol: str) -> int:
    # Dukascopy wiki: 100000 for most FX pairs, 1000 for JPY pairs.
    s = symbol.upper().strip()
    return 1000 if s.endswith("JPY") else 100000


def ticks_url(symbol: str, date: dt.date) -> str:
    # Daily tick file: SYMBOL/YEAR/MONTH/DAY_ticks.bi5 (month is 0-indexed)
    return f"{DUKASCOPY_BASE_URL}/{symbol}/{date.year}/{month_zero_based(date)}/{date.day:02d}_ticks.bi5"


def raw_tick_path(raw_root: Path, symbol: str, date: dt.date) -> Path:
    return raw_root / "dukascopy_ticks" / symbol / str(date.year) / month_zero_based(date) / f"{date.day:02d}_ticks.bi5"


def _retry_sleep_s(attempt: int, base_backoff_s: float, max_backoff_s: float, retry_after: Optional[str]) -> float:
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass
    exp = base_backoff_s * (2**attempt)
    jitter = random.random() * min(1.0, base_backoff_s)
    return min(max_backoff_s, exp + jitter)


def download_with_retry(
    url: str,
    out_path: Path,
    *,
    timeout_s: int,
    max_retries: int = 8,
    base_backoff_s: float = 1.0,
    max_backoff_s: float = 120.0,
    user_agent: str = "vanta-research/0.1 (ticks)",
) -> bool:
    if out_path.exists() and out_path.stat().st_size > 0:
        return True

    out_path.parent.mkdir(parents=True, exist_ok=True)
    last_error: Optional[Exception] = None

    for attempt in range(0, max_retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": user_agent})
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                data = resp.read()
            tmp = out_path.with_suffix(out_path.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(out_path)
            return True
        except urllib.error.HTTPError as e:
            last_error = e
            code = int(getattr(e, "code", 0) or 0)
            retry_after = getattr(e, "headers", {}).get("Retry-After") if hasattr(e, "headers") else None
            if code == 404:
                # No ticks for this day (weekends/holidays) -> treat as missing, not fatal.
                return False
            if code not in RETRYABLE_HTTP_CODES or attempt >= max_retries:
                raise
            time.sleep(_retry_sleep_s(attempt, base_backoff_s, max_backoff_s, retry_after))
        except (socket.timeout, TimeoutError, urllib.error.URLError) as e:
            last_error = e
            if attempt >= max_retries:
                raise
            time.sleep(_retry_sleep_s(attempt, base_backoff_s, max_backoff_s, retry_after=None))

    raise last_error or RuntimeError("download failed")


def iter_ticks(path: Path, *, symbol: str, day: dt.date) -> Iterator[Tuple[dt.datetime, float, float]]:
    """Yield (ts_utc, bid, ask) for a single day."""
    compressed = path.read_bytes()
    raw = lzma.decompress(compressed, format=lzma.FORMAT_ALONE)
    rec_size = 20
    if len(raw) % rec_size != 0:
        raise ValueError(f"Unexpected decompressed size for {path}")

    pv = float(point_value(symbol))
    day_start = dt.datetime(day.year, day.month, day.day, tzinfo=dt.timezone.utc)

    for off in range(0, len(raw), rec_size):
        ms, ask_u32, bid_u32, _ask_vol, _bid_vol = struct.unpack(">IIIff", raw[off : off + rec_size]
        )
        ts = day_start + dt.timedelta(milliseconds=int(ms))
        ask = float(ask_u32) / pv
        bid = float(bid_u32) / pv
        yield ts, bid, ask


@dataclass
class EventKey:
    event_id: str
    scheduled_at_utc: dt.datetime
    currency: str
    impact: str
    event_name: str
    symbol: str




def impact_matches(impact: str, impact_filter: Optional[Sequence[str]]) -> bool:
    if not impact_filter:
        return True
    imp = (impact or "").strip().lower()
    for f in impact_filter:
        ff = str(f or "").strip().lower()
        if not ff:
            continue
        if imp == ff or imp.startswith(ff):
            return True
    return False


def _pair_currencies(symbol: str) -> Tuple[str, str]:
    s = symbol.upper().strip()
    if len(s) < 6:
        raise ValueError(f"Symbol too short to parse currencies: {symbol!r}")
    return s[0:3], s[3:6]


def load_event_keys_from_sqlite(
    db_path: Path,
    *,
    events_start: Optional[dt.date],
    events_end: Optional[dt.date],
    symbols: Sequence[str],
    impact_filter: Optional[Sequence[str]],
    max_rows: Optional[int],
) -> List[EventKey]:
    """Load events from SQLite and expand them to (event, symbol) rows."""

    if not db_path.exists():
        raise FileNotFoundError(f"DB not found: {db_path}")

    symbols_u = [s.upper().strip() for s in symbols if str(s).strip()]
    if not symbols_u:
        return []

    cur_to_symbols: Dict[str, List[str]] = {}
    for sym in symbols_u:
        base, quote = _pair_currencies(sym)
        cur_to_symbols.setdefault(base, []).append(sym)
        cur_to_symbols.setdefault(quote, []).append(sym)

    params: List[object] = []
    where: List[str] = []

    if events_start:
        where.append("scheduled_at_utc >= ?")
        params.append(f"{events_start.isoformat()}T00:00:00+00:00")
    if events_end:
        where.append("scheduled_at_utc <= ?")
        params.append(f"{events_end.isoformat()}T23:59:59+00:00")

    sql = "select id as event_id, scheduled_at_utc, currency, impact, event_name from events"
    if where:
        sql += " where " + " and ".join(where)
    sql += " order by scheduled_at_utc asc"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    out: List[EventKey] = []
    try:
        for row in conn.execute(sql, params):
            currency = str(row["currency"] or "").strip().upper()
            if not currency:
                continue

            impact = str(row["impact"] or "").strip()
            if not impact_matches(impact, impact_filter):
                continue

            ts_raw = str(row["scheduled_at_utc"] or "").strip()
            if not ts_raw:
                continue

            t0 = dt.datetime.fromisoformat(ts_raw)
            if t0.tzinfo is None:
                t0 = t0.replace(tzinfo=dt.timezone.utc)
            else:
                t0 = t0.astimezone(dt.timezone.utc)

            syms = cur_to_symbols.get(currency, [])
            if not syms:
                continue

            for sym in syms:
                out.append(
                    EventKey(
                        event_id=str(row["event_id"]),
                        scheduled_at_utc=t0,
                        currency=currency,
                        impact=impact,
                        event_name=str(row["event_name"] or "").strip(),
                        symbol=sym,
                    )
                )
                if max_rows and len(out) >= max_rows:
                    return out
    finally:
        conn.close()

    return out


def load_event_keys(
    csv_path: Path,
    *,
    events_start: Optional[dt.date],
    events_end: Optional[dt.date],
    symbols_filter: Optional[set],
    impact_filter: Optional[Sequence[str]],
    max_rows: Optional[int],
) -> List[EventKey]:
    out: List[EventKey] = []
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sym = str(row.get("symbol", "")).strip().upper()
            if not sym:
                continue
            if symbols_filter and sym not in symbols_filter:
                continue

            t0 = dt.datetime.fromisoformat(str(row.get("scheduled_at_utc", "")).strip())
            if t0.tzinfo is None:
                t0 = t0.replace(tzinfo=dt.timezone.utc)
            else:
                t0 = t0.astimezone(dt.timezone.utc)

            d = t0.date()
            if events_start and d < events_start:
                continue
            if events_end and d > events_end:
                continue


            impact = str(row.get("impact", "")).strip()
            if not impact_matches(impact, impact_filter):
                continue
            out.append(
                EventKey(
                    event_id=str(row.get("event_id", "")).strip(),
                    scheduled_at_utc=t0,
                    currency=str(row.get("currency", "")).strip().upper(),
                    impact=impact,
                    event_name=str(row.get("event_name", "")).strip(),
                    symbol=sym,
                )
            )
            if max_rows and len(out) >= max_rows:
                break
    return out


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    k = (len(xs) - 1) * (p / 100.0)
    f = int(k)
    c = min(len(xs) - 1, f + 1)
    if f == c:
        return float(xs[f])
    d0 = xs[f] * (c - k)
    d1 = xs[c] * (k - f)
    return float(d0 + d1)


def analyze_day(
    *,
    symbol: str,
    day: dt.date,
    tick_path: Path,
    events: List[EventKey],
    window_minutes: int,
    pre_minutes: int,
    trigger_pips: float,
) -> List[Dict[str, object]]:
    """Analyze all events for a given (symbol, day) in a single tick scan."""

    pip = pip_size(symbol)
    trigger_price = float(trigger_pips) * float(pip)
    pre_delta = dt.timedelta(minutes=int(pre_minutes))
    win_delta = dt.timedelta(minutes=int(window_minutes))

    # Build event state
    items: List[Dict[str, object]] = []
    for e in events:
        t0 = e.scheduled_at_utc
        items.append(
            {
                "e": e,
                "t0": t0,
                "pre_start": t0 - pre_delta,
                "end": t0 + win_delta,
                "mid0": None,
                "last_mid": None,
                "triggered": False,
                "dir": 0,
                "trigger_time": None,
                "peak_move": 0.0,
                "peak_time": None,
                "retrace_time": None,
                "done": False,
                "max_spread": 0.0,
            }
        )

    items.sort(key=lambda x: x["pre_start"])  # type: ignore[index]
    active: List[Dict[str, object]] = []
    idx = 0

    for ts, bid, ask in iter_ticks(tick_path, symbol=symbol, day=day):
        # activate new events
        while idx < len(items) and ts >= items[idx]["pre_start"]:  # type: ignore[index]
            active.append(items[idx])
            idx += 1

        if not active and idx >= len(items):
            # No remaining events for this day
            if ts > (items[-1]["end"] if items else ts):
                break

        # update active events
        mid = (bid + ask) / 2.0
        spread = max(0.0, ask - bid)

        still_active: List[Dict[str, object]] = []
        for s in active:
            if s["done"]:
                continue

            t0 = s["t0"]
            end = s["end"]
            if ts > end:
                s["done"] = True
                continue

            # track last mid before t0
            if ts <= t0:
                s["last_mid"] = mid
                # keep tracking spread pre-event too
                if spread > s["max_spread"]:
                    s["max_spread"] = spread
                still_active.append(s)
                continue

            # after t0
            if s["mid0"] is None:
                s["mid0"] = s["last_mid"] if s["last_mid"] is not None else mid

            mid0 = float(s["mid0"])  # type: ignore[arg-type]
            if spread > s["max_spread"]:
                s["max_spread"] = spread

            if not s["triggered"]:
                if mid >= mid0 + trigger_price:
                    s["triggered"] = True
                    s["dir"] = 1
                    s["trigger_time"] = ts
                    s["peak_move"] = max(float(s["peak_move"]), mid - mid0)
                    s["peak_time"] = ts
                elif mid <= mid0 - trigger_price:
                    s["triggered"] = True
                    s["dir"] = -1
                    s["trigger_time"] = ts
                    s["peak_move"] = max(float(s["peak_move"]), mid0 - mid)
                    s["peak_time"] = ts
            else:
                direction = int(s["dir"])
                if direction == 1:
                    move = mid - mid0
                    if move > float(s["peak_move"]):
                        s["peak_move"] = move
                        s["peak_time"] = ts
                    if mid <= mid0:
                        s["retrace_time"] = ts
                        s["done"] = True
                else:
                    move = mid0 - mid
                    if move > float(s["peak_move"]):
                        s["peak_move"] = move
                        s["peak_time"] = ts
                    if mid >= mid0:
                        s["retrace_time"] = ts
                        s["done"] = True

            still_active.append(s)

        active = still_active

    # finalize
    rows: List[Dict[str, object]] = []
    for s in items:
        e: EventKey = s["e"]  # type: ignore[assignment]
        t0: dt.datetime = s["t0"]  # type: ignore[assignment]
        mid0 = s["mid0"]
        triggered = bool(s["triggered"])
        run_pips = float(s["peak_move"]) / pip if triggered else 0.0

        peak_time: Optional[dt.datetime] = s["peak_time"]  # type: ignore[assignment]
        retrace_time: Optional[dt.datetime] = s["retrace_time"]  # type: ignore[assignment]

        time_to_peak_s = (peak_time - t0).total_seconds() if peak_time else None
        time_to_retrace_s = (retrace_time - t0).total_seconds() if retrace_time else None

        rows.append(
            {
                "event_id": e.event_id,
                "scheduled_at_utc": e.scheduled_at_utc.isoformat(),
                "currency": e.currency,
                "impact": e.impact,
                "event_name": e.event_name,
                "symbol": e.symbol,
                "window_minutes": int(window_minutes),
                "trigger_pips": float(trigger_pips),
                "mid0": mid0,
                "dir": int(s["dir"]),
                "run_pips": float(run_pips),
                "time_to_peak_s": time_to_peak_s,
                "time_to_retrace_s": time_to_retrace_s,
                "max_spread_pips": float(s["max_spread"]) / pip,
                "had_ticks": True,
            }
        )

    return rows


def main() -> int:
    p = argparse.ArgumentParser(description="Analyze post-event impulse using Dukascopy ticks (daily .bi5)")
    p.add_argument(
        "--events-source",
        choices=["csv", "sqlite"],
        default="csv",
        help="Where to load events from (csv=event_reactions_m1.csv, sqlite=data/vanta.sqlite)",
    )
    p.add_argument("--input", default="data/processed/research/event_reactions_m1.csv", help="Input CSV (used when --events-source csv)")
    p.add_argument("--db", default="data/vanta.sqlite", help="SQLite DB path (used when --events-source sqlite)")
    p.add_argument("--impact", nargs="*", default=[], help="Impact filter prefixes (e.g. High)")
    p.add_argument("--symbols", nargs="*", default=[], help="Only analyze these symbols")
    p.add_argument("--events-start", default=None, help="YYYY-MM-DD")
    p.add_argument("--events-end", default=None, help="YYYY-MM-DD")
    p.add_argument("--window-minutes", type=int, default=60)
    p.add_argument("--pre-minutes", type=int, default=5)
    p.add_argument("--trigger-pips", type=float, default=0.5)
    p.add_argument("--raw-root", default="data/raw/price_data", help="Raw root (stores dukascopy_ticks)")
    p.add_argument("--timeout", type=int, default=30)
    p.add_argument("--max-rows", type=int, default=0, help="Limit number of input rows (0=all)")
    p.add_argument("--out-per-event", default="data/processed/research/impulse_ticks_per_event.csv")
    p.add_argument("--out-summary", default="data/processed/research/impulse_ticks_summary.json")
    p.add_argument("--quiet", action="store_true", help="Reduce console output")
    args = p.parse_args()

    def log(msg: str) -> None:
        if not args.quiet:
            print(msg, flush=True)


    input_csv = Path(str(args.input))
    raw_root = Path(str(args.raw_root))

    events_start = dt.date.fromisoformat(args.events_start) if args.events_start else None
    events_end = dt.date.fromisoformat(args.events_end) if args.events_end else None
    symbols_filter = set([s.upper() for s in args.symbols]) if args.symbols else None
    max_rows = int(args.max_rows) if int(args.max_rows) > 0 else None
    impact_filter = [s for s in args.impact if str(s).strip()] if args.impact else None

    if str(args.events_source).lower() == "sqlite":
        if not symbols_filter:
            raise SystemExit("--symbols is required when --events-source sqlite")
        db_path = Path(str(args.db))
        keys = load_event_keys_from_sqlite(
            db_path,
            events_start=events_start,
            events_end=events_end,
            symbols=sorted(symbols_filter),
            impact_filter=impact_filter,
            max_rows=max_rows,
        )
        input_label = str(db_path)
    else:
        keys = load_event_keys(
            input_csv,
            events_start=events_start,
            events_end=events_end,
            symbols_filter=symbols_filter,
            impact_filter=impact_filter,
            max_rows=max_rows,
        )
        input_label = str(input_csv)

    if not keys:
        print("No events to analyze.")
        return 0
    # Group by (symbol, day)
    by_group: Dict[Tuple[str, dt.date], List[EventKey]] = {}
    for k in keys:
        g = (k.symbol, k.scheduled_at_utc.date())
        by_group.setdefault(g, []).append(k)

    groups = sorted(by_group.items(), key=lambda x: (x[0][0], x[0][1]))
    log(f"Loaded {len(keys)} event rows -> {len(groups)} (symbol, day) groups")

    out_rows: List[Dict[str, object]] = []
    missing_files = 0

    for gi, ((sym, day), group) in enumerate(groups, start=1):
        url = ticks_url(sym, day)
        path = raw_tick_path(raw_root, sym, day)

        cached = path.exists() and path.stat().st_size > 0
        if cached:
            log(f"[{gi}/{len(groups)}] {sym} {day} ({len(group)} events) tickfile cached")
        else:
            log(f"[{gi}/{len(groups)}] {sym} {day} ({len(group)} events) downloading ticks...")

        t_start = time.time()
        try:
            ok = download_with_retry(url, path, timeout_s=int(args.timeout))
        except Exception as ex:  # noqa: BLE001
            log(f"Download failed for {sym} {day}: {ex}")
            missing_files += 1
            for e in group:
                out_rows.append(
                    {
                        "event_id": e.event_id,
                        "scheduled_at_utc": e.scheduled_at_utc.isoformat(),
                        "currency": e.currency,
                        "impact": e.impact,
                        "event_name": e.event_name,
                        "symbol": e.symbol,
                        "window_minutes": int(args.window_minutes),
                        "trigger_pips": float(args.trigger_pips),
                        "mid0": None,
                        "dir": 0,
                        "run_pips": 0.0,
                        "time_to_peak_s": None,
                        "time_to_retrace_s": None,
                        "max_spread_pips": None,
                        "had_ticks": False,
                    }
                )
            continue

        elapsed = time.time() - t_start
        if ok and path.exists():
            if cached:
                log(f"    cache hit ({elapsed:.2f}s)")
            else:
                try:
                    size_mb = path.stat().st_size / (1024 * 1024)
                    log(f"    downloaded {size_mb:.1f} MB in {elapsed:.1f}s")
                except Exception:
                    pass

        if not ok or not path.exists():
            missing_files += 1
            for e in group:
                out_rows.append(
                    {
                        "event_id": e.event_id,
                        "scheduled_at_utc": e.scheduled_at_utc.isoformat(),
                        "currency": e.currency,
                        "impact": e.impact,
                        "event_name": e.event_name,
                        "symbol": e.symbol,
                        "window_minutes": int(args.window_minutes),
                        "trigger_pips": float(args.trigger_pips),
                        "mid0": None,
                        "dir": 0,
                        "run_pips": 0.0,
                        "time_to_peak_s": None,
                        "time_to_retrace_s": None,
                        "max_spread_pips": None,
                        "had_ticks": False,
                    }
                )
            continue

        try:
            out_rows.extend(
                analyze_day(
                    symbol=sym,
                    day=day,
                    tick_path=path,
                    events=group,
                    window_minutes=int(args.window_minutes),
                    pre_minutes=int(args.pre_minutes),
                    trigger_pips=float(args.trigger_pips),
                )
            )
            log(f"    analyzed {len(group)} events")
        except Exception as ex:  # noqa: BLE001
            log(f"Decode/analyze failed for {sym} {day}: {ex}")
            missing_files += 1

    # Write per-event CSV
    out_csv = Path(str(args.out_per_event))
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "event_id",
        "scheduled_at_utc",
        "currency",
        "impact",
        "event_name",
        "symbol",
        "window_minutes",
        "trigger_pips",
        "mid0",
        "dir",
        "run_pips",
        "time_to_peak_s",
        "time_to_retrace_s",
        "max_spread_pips",
        "had_ticks",
    ]
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in out_rows:
            w.writerow({k: r.get(k) for k in fieldnames})

    # Summary
    tick_rows = [r for r in out_rows if r.get("had_ticks")]
    run_values_all = [float(r["run_pips"]) for r in tick_rows if r.get("run_pips") is not None]
    triggered_rows = [r for r in tick_rows if int(r.get("dir") or 0) != 0]
    run_values_triggered = [float(r["run_pips"]) for r in triggered_rows if r.get("run_pips") is not None]
    spread_values = [float(r["max_spread_pips"]) for r in tick_rows if r.get("max_spread_pips") is not None]

    summary = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "input": str(input_label),
        "out_per_event": str(out_csv),
        "events": len(out_rows),
        "groups": len(groups),
        "missing_files": int(missing_files),
        "window_minutes": int(args.window_minutes),
        "pre_minutes": int(args.pre_minutes),
        "trigger_pips": float(args.trigger_pips),
        "trigger_rate": (len(run_values_triggered) / len(run_values_all)) if run_values_all else 0.0,
        "run_pips_all": {
            "count": len(run_values_all),
            "median": median(run_values_all) if run_values_all else 0.0,
            "mean": mean(run_values_all) if run_values_all else 0.0,
            "p75": percentile(run_values_all, 75) if run_values_all else 0.0,
            "p90": percentile(run_values_all, 90) if run_values_all else 0.0,
            "p95": percentile(run_values_all, 95) if run_values_all else 0.0,
        },
        "run_pips_triggered": {
            "count": len(run_values_triggered),
            "median": median(run_values_triggered) if run_values_triggered else 0.0,
            "mean": mean(run_values_triggered) if run_values_triggered else 0.0,
            "p75": percentile(run_values_triggered, 75) if run_values_triggered else 0.0,
            "p90": percentile(run_values_triggered, 90) if run_values_triggered else 0.0,
            "p95": percentile(run_values_triggered, 95) if run_values_triggered else 0.0,
        },
        "max_spread_pips": {
            "count": len(spread_values),
            "median": median(spread_values) if spread_values else 0.0,
            "p90": percentile(spread_values, 90) if spread_values else 0.0,
            "p95": percentile(spread_values, 95) if spread_values else 0.0,
        },
    }

    out_summary = Path(str(args.out_summary))
    out_summary.parent.mkdir(parents=True, exist_ok=True)
    out_summary.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"Wrote per-event: {out_csv}")
    print(f"Wrote summary: {out_summary}")
    print(f"Events analyzed: {len(out_rows)} (tick-backed: {len(run_values_all)}, triggered: {len(run_values_triggered)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())











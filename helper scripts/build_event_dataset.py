"""Build an event↔price reaction research dataset (Phase 4).

This script joins:
- Economic events in SQLite (`data/vanta.sqlite`)
- Dukascopy bid/ask minute candles (`data/processed/price_data/dukascopy_m1/...`)

It produces a *working* research dataset suitable for initial analysis and
backtesting.

Design goals (from project overview)
- Match each event to the relevant currency pairs.
- Use a pre-event reference price.
- Compute post-event returns for multiple horizons.
- Record missing data rather than silently dropping it.

Implementation notes
- Uses only stdlib (sqlite3 + csv) so it runs in a fresh environment.
- Operates on the pairs you already downloaded; missing pairs are skipped.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


RET_HORIZONS_MIN = (1, 5, 15, 30, 60, 240)


@dataclass(frozen=True)
class Quote:
    bid_close: float
    ask_close: float

    @property
    def mid_close(self) -> float:
        return (self.bid_close + self.ask_close) / 2.0

    @property
    def spread(self) -> float:
        return self.ask_close - self.bid_close


def parse_ts(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def truncate_to_minute(value: dt.datetime) -> dt.datetime:
    return value.replace(second=0, microsecond=0)


def list_available_symbols(prices_root: Path) -> List[str]:
    root = prices_root
    if (root / "dukascopy_m1").exists():
        root = root / "dukascopy_m1"
    symbols = [p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")]
    symbols.sort()
    return symbols


def symbol_contains_currency(symbol: str, currency: str) -> bool:
    s = symbol.upper()
    c = currency.upper()
    return len(s) == 6 and (s[:3] == c or s[3:] == c)


def symbol_day_path(prices_root: Path, symbol: str, day: dt.date) -> Path:
    base = prices_root
    if (base / "dukascopy_m1").exists():
        base = base / "dukascopy_m1"
    return base / symbol / f"{day.year:04d}" / f"{day.month:02d}" / f"{day.day:02d}.csv"


class DayCache:
    def __init__(self, prices_root: Path, max_days: int = 12) -> None:
        self.prices_root = prices_root
        self.max_days = max_days
        self._cache: Dict[Tuple[str, dt.date], Dict[dt.datetime, Quote]] = {}
        self._order: List[Tuple[str, dt.date]] = []

    def get_day(self, symbol: str, day: dt.date) -> Optional[Dict[dt.datetime, Quote]]:
        key = (symbol, day)
        if key in self._cache:
            return self._cache[key]

        path = symbol_day_path(self.prices_root, symbol, day)
        if not path.exists():
            return None

        mapping: Dict[dt.datetime, Quote] = {}
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ts = parse_ts((row.get("timestamp_utc") or "").strip())
                bid = row.get("bid_close")
                ask = row.get("ask_close")
                if bid is None or ask is None or bid == "" or ask == "":
                    continue
                try:
                    mapping[ts] = Quote(bid_close=float(bid), ask_close=float(ask))
                except ValueError:
                    continue

        self._cache[key] = mapping
        self._order.append(key)
        if len(self._order) > self.max_days:
            old = self._order.pop(0)
            self._cache.pop(old, None)
        return mapping

    def get_quote(self, symbol: str, ts_utc: dt.datetime) -> Optional[Quote]:
        day = ts_utc.date()
        day_map = self.get_day(symbol, day)
        if day_map is None:
            return None
        return day_map.get(ts_utc)


def load_latest_run_id(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "select max(id) from collection_runs where source_name = 'economic_calendar'"
    ).fetchone()
    if row is None or row[0] is None:
        raise RuntimeError("No collection_runs found for source_name='economic_calendar'")
    return int(row[0])


def iter_events(conn: sqlite3.Connection, run_id: int) -> Iterable[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    sql = """
    select
      e.id as event_id,
      e.scheduled_at_utc,
      e.currency,
      e.impact,
      e.event_name,
      o.actual_raw,
      o.forecast_raw,
      o.previous_raw,
      o.actual_value,
      o.forecast_value,
      o.previous_value,
      o.actual_unit,
      o.forecast_unit,
      o.previous_unit
    from events e
    join event_observations o on o.event_id = e.id
    where o.collected_run_id = ?
    order by e.scheduled_at_utc asc
    """
    yield from conn.execute(sql, (run_id,))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build event↔price reaction dataset")
    parser.add_argument("--db", default="data/vanta.sqlite", help="SQLite DB path")
    parser.add_argument(
        "--prices-root",
        default="data/processed/price_data",
        help="Processed price data root (contains dukascopy_m1)",
    )
    parser.add_argument(
        "--out",
        default="data/processed/research/event_reactions_m1.csv",
        help="Output dataset CSV",
    )
    parser.add_argument(
        "--out-qc",
        default="data/processed/research/event_reactions_qc.json",
        help="QC report JSON",
    )
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help="Optional subset of symbols to use (default: all available in prices-root)",
    )
    parser.add_argument(
        "--events-start",
        default="",
        help="Only include events on/after this UTC date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--events-end",
        default="",
        help="Only include events on/before this UTC date (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--max-events",
        type=int,
        default=0,
        help="If non-zero, only process the first N events",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    prices_root = Path(args.prices_root)
    out_path = Path(args.out)
    qc_path = Path(args.out_qc)

    if not db_path.exists():
        raise SystemExit(f"DB not found: {db_path}")
    if not prices_root.exists():
        raise SystemExit(f"Prices root not found: {prices_root}")

    symbols = [s.upper() for s in (args.symbols or list_available_symbols(prices_root))]
    if not symbols:
        raise SystemExit("No symbols found in prices-root")

    events_start_date: Optional[dt.date] = dt.date.fromisoformat(args.events_start) if args.events_start else None
    events_end_date: Optional[dt.date] = dt.date.fromisoformat(args.events_end) if args.events_end else None

    conn = sqlite3.connect(db_path)
    run_id = load_latest_run_id(conn)

    day_cache = DayCache(prices_root=prices_root)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    qc_path.parent.mkdir(parents=True, exist_ok=True)

    missing_counts = Counter()
    rows_written = 0
    events_seen = 0

    fieldnames = [
        "event_id",
        "scheduled_at_utc",
        "currency",
        "impact",
        "event_name",
        "symbol",
        "ref_timestamp_utc",
        "ref_mid_close",
        "ref_spread",
        "actual_raw",
        "forecast_raw",
        "previous_raw",
        "actual_value",
        "forecast_value",
        "previous_value",
        "actual_unit",
        "forecast_unit",
        "previous_unit",
    ]
    for m in RET_HORIZONS_MIN:
        fieldnames.append(f"ret_{m}m")

    with out_path.open("w", encoding="utf-8", newline="") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=fieldnames)
        writer.writeheader()

        for event in iter_events(conn, run_id):
            events_seen += 1
            if args.max_events and events_seen > args.max_events:
                break

            currency = (event["currency"] or "").strip().upper()
            scheduled_at = parse_ts(event["scheduled_at_utc"])

            if events_start_date and scheduled_at.date() < events_start_date:
                missing_counts["event_before_range"] += 1
                continue
            if events_end_date and scheduled_at.date() > events_end_date:
                missing_counts["event_after_range"] += 1
                continue

            event_minute = truncate_to_minute(scheduled_at)
            ref_ts = event_minute - dt.timedelta(minutes=1)

            # Determine which downloaded symbols this event can affect.
            event_symbols = [s for s in symbols if symbol_contains_currency(s, currency)]
            if not event_symbols:
                missing_counts["no_matching_symbol"] += 1
                continue

            for sym in event_symbols:
                ref_quote = day_cache.get_quote(sym, ref_ts)
                if ref_quote is None:
                    missing_counts["missing_ref_quote"] += 1
                    # Still write a row; keep missing values explicit.
                    row: Dict[str, object] = {
                        "event_id": event["event_id"],
                        "scheduled_at_utc": event["scheduled_at_utc"],
                        "currency": currency,
                        "impact": event["impact"],
                        "event_name": event["event_name"],
                        "symbol": sym,
                        "ref_timestamp_utc": ref_ts.isoformat(),
                        "ref_mid_close": "",
                        "ref_spread": "",
                        "actual_raw": event["actual_raw"],
                        "forecast_raw": event["forecast_raw"],
                        "previous_raw": event["previous_raw"],
                        "actual_value": event["actual_value"],
                        "forecast_value": event["forecast_value"],
                        "previous_value": event["previous_value"],
                        "actual_unit": event["actual_unit"],
                        "forecast_unit": event["forecast_unit"],
                        "previous_unit": event["previous_unit"],
                    }
                    for m in RET_HORIZONS_MIN:
                        row[f"ret_{m}m"] = ""
                    writer.writerow(row)
                    rows_written += 1
                    continue

                ref_mid = ref_quote.mid_close

                row = {
                    "event_id": event["event_id"],
                    "scheduled_at_utc": event["scheduled_at_utc"],
                    "currency": currency,
                    "impact": event["impact"],
                    "event_name": event["event_name"],
                    "symbol": sym,
                    "ref_timestamp_utc": ref_ts.isoformat(),
                    "ref_mid_close": ref_mid,
                    "ref_spread": ref_quote.spread,
                    "actual_raw": event["actual_raw"],
                    "forecast_raw": event["forecast_raw"],
                    "previous_raw": event["previous_raw"],
                    "actual_value": event["actual_value"],
                    "forecast_value": event["forecast_value"],
                    "previous_value": event["previous_value"],
                    "actual_unit": event["actual_unit"],
                    "forecast_unit": event["forecast_unit"],
                    "previous_unit": event["previous_unit"],
                }

                for m in RET_HORIZONS_MIN:
                    ts = event_minute + dt.timedelta(minutes=m)
                    q = day_cache.get_quote(sym, ts)
                    if q is None:
                        missing_counts[f"missing_ret_{m}m"] += 1
                        row[f"ret_{m}m"] = ""
                    else:
                        row[f"ret_{m}m"] = (q.mid_close - ref_mid) / ref_mid

                writer.writerow(row)
                rows_written += 1

    qc = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_db": str(db_path.as_posix()),
        "calendar_run_id": run_id,
        "prices_root": str(prices_root.as_posix()),
        "symbols_used": symbols,
        "events_start": events_start_date.isoformat() if events_start_date else None,
        "events_end": events_end_date.isoformat() if events_end_date else None,
        "events_seen": events_seen,
        "rows_written": rows_written,
        "missing_counts": missing_counts.most_common(),
        "ret_horizons_min": list(RET_HORIZONS_MIN),
        "out": str(out_path.as_posix()),
    }
    qc_path.write_text(json.dumps(qc, indent=2, sort_keys=True), encoding="utf-8")

    print("Wrote dataset:", out_path)
    print("Wrote QC report:", qc_path)
    print("Rows:", rows_written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


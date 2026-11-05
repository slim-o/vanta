"""Run Dukascopy M1 downloads in month-sized batches.

Why this exists
- Large downloads (28 pairs x multiple years) are easier to resume month-by-month.
- Writes a small marker file per month so re-runs can skip completed months.

This is a thin wrapper around `helper scripts/download_prices.py`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple

import download_prices


# Rough priority order for getting a usable research dataset quickly.
# Starts with the most liquid USD majors, then other highly traded crosses.
PRIORITY_SYMBOLS = [
    "EURUSD",
    "USDJPY",
    "GBPUSD",
    "AUDUSD",
    "USDCAD",
    "USDCHF",
    "NZDUSD",
    "EURJPY",
    "GBPJPY",
    "EURGBP",
    "EURCHF",
    "EURCAD",
    "EURAUD",
    "AUDJPY",
    "CADJPY",
    "CHFJPY",
    "NZDJPY",
    "GBPCAD",
    "GBPCHF",
    "GBPAUD",
    "GBPNZD",
    "AUDCAD",
    "AUDCHF",
    "AUDNZD",
    "NZDCAD",
    "NZDCHF",
    "CADCHF",
    "EURNZD",
]


def parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def month_start(d: dt.date) -> dt.date:
    return dt.date(d.year, d.month, 1)


def month_end(d: dt.date) -> dt.date:
    # First of next month minus one day.
    if d.month == 12:
        n = dt.date(d.year + 1, 1, 1)
    else:
        n = dt.date(d.year, d.month + 1, 1)
    return n - dt.timedelta(days=1)


def iter_month_batches(start: dt.date, end: dt.date) -> Iterable[Tuple[dt.date, dt.date]]:
    cur = month_start(start)
    while cur <= end:
        b_start = max(start, cur)
        b_end = min(end, month_end(cur))
        yield b_start, b_end
        # Advance to next month.
        cur = month_end(cur) + dt.timedelta(days=1)


def marker_path(processed_root: Path, batch_start: dt.date) -> Path:
    return processed_root / "dukascopy_m1" / "_batches" / f"{batch_start.year:04d}-{batch_start.month:02d}.json"


def marker_path_symbol(processed_root: Path, symbol: str, batch_start: dt.date) -> Path:
    return (
        processed_root
        / "dukascopy_m1"
        / "_batches"
        / symbol
        / f"{batch_start.year:04d}-{batch_start.month:02d}.json"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch runner for Dukascopy M1 downloads")
    parser.add_argument("--start", required=True, type=parse_date, help="Start date (YYYY-MM-DD, UTC)")
    parser.add_argument("--end", required=True, type=parse_date, help="End date (YYYY-MM-DD, UTC)")
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=None,
        help="Symbols list; if omitted, download_prices.py default is used (28 majors)",
    )
    parser.add_argument(
        "--mode",
        choices=["by-symbol", "by-month"],
        default="by-symbol",
        help="Download order: by-symbol finishes top pairs sooner; by-month is legacy.",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=0,
        help="If non-zero, only download the first N symbols in the chosen order",
    )
    parser.add_argument(
        "--start-from",
        default="",
        help="Start from this symbol in the chosen order (e.g. GBPUSD)",
    )
    parser.add_argument("--raw-root", default="data/raw/price_data", help="Raw data root")
    parser.add_argument("--processed-root", default="data/processed/price_data", help="Processed data root")
    parser.add_argument("--workers", type=int, default=1, help="Passed to download_prices.py")
    parser.add_argument("--sleep", type=float, default=1.0, help="Passed to download_prices.py")
    parser.add_argument("--timeout", type=int, default=60, help="Passed to download_prices.py")
    parser.add_argument("--max-retries", type=int, default=12, help="Passed to download_prices.py")
    parser.add_argument("--backoff", type=float, default=2.0, help="Passed to download_prices.py")
    parser.add_argument("--max-backoff", type=float, default=180.0, help="Passed to download_prices.py")
    parser.add_argument(
        "--http-concurrency",
        type=int,
        default=1,
        help="Max concurrent HTTP requests (increase carefully; default 1 is safest)",
    )
    parser.add_argument("--user-agent", default=None, help="Passed to download_prices.py")
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Show a live multi-line progress dashboard (overall + per-symbol)",
    )
    parser.add_argument(
        "--progress-refresh",
        type=float,
        default=2.0,
        help="Minimum seconds between dashboard refreshes",
    )
    parser.add_argument(
        "--no-ansi",
        action="store_true",
        help="Disable ANSI cursor control (prints periodic snapshots instead)",
    )
    parser.add_argument(
        "--error-log",
        default="data/processed/price_data/dukascopy_m1/_errors.jsonl",
        help="JSONL file to append failed job details to",
    )
    parser.add_argument("--force", action="store_true", help="Re-run even if month marker exists")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue to next month on failure")
    args = parser.parse_args()

    error_log_path = Path(args.error_log) if args.error_log else None

    processed_root = Path(args.processed_root)
    processed_root.mkdir(parents=True, exist_ok=True)

    # Choose symbol ordering.
    if args.symbols:
        symbols = [s.upper() for s in args.symbols]
    else:
        # Prefer our priority order when running by-symbol.
        if args.mode == "by-symbol":
            symbols = [s for s in PRIORITY_SYMBOLS]
        else:
            symbols = list(download_prices.MAJOR_PAIRS_28)

    if args.top and args.top > 0:
        symbols = symbols[: args.top]

    if args.start_from:
        start_sym = args.start_from.upper().strip()
        if start_sym in symbols:
            symbols = symbols[symbols.index(start_sym) :]
        else:
            print(f"Warning: --start-from {start_sym} not found in symbols list")

    total_days = (args.end - args.start).days + 1
    total_jobs = total_days * len(symbols)
    done_jobs = 0

    per_symbol_total = {s: total_days for s in symbols}
    per_symbol_done = {s: 0 for s in symbols}
    counts = {"ok": 0, "missing": 0, "failed": 0}

    metrics = download_prices.DownloadMetrics()

    started = time.time()
    last_refresh = 0.0

    def _bar(done: int, total_n: int, width: int = 20) -> str:
        if total_n <= 0:
            return "[" + "." * width + "]"
        filled = int(width * done / total_n)
        filled = max(0, min(width, filled))
        return "[" + "#" * filled + "." * (width - filled) + "]"

    def _pct(done: int, total_n: int) -> float:
        return 0.0 if total_n <= 0 else (100.0 * done / total_n)

    def _eta(elapsed_s: float, done: int, total_n: int) -> str:
        if done <= 0:
            return "--:--"
        rate = done / max(0.001, elapsed_s)
        rem = max(0, total_n - done)
        eta_s = int(rem / max(0.0001, rate))
        mins, secs = divmod(eta_s, 60)
        hrs, mins = divmod(mins, 60)
        if hrs:
            return f"{hrs:d}:{mins:02d}:{secs:02d}"
        return f"{mins:02d}:{secs:02d}"

    def _render(force: bool = False) -> None:
        nonlocal last_refresh
        if not args.progress:
            return
        now = time.time()
        if not force and (now - last_refresh) < max(0.1, args.progress_refresh):
            return
        last_refresh = now

        elapsed = now - started
        snap = metrics.snapshot()
        c = snap["counters"]
        r429 = int(c.get("http_429", 0))
        r503 = int(c.get("http_503", 0))
        timeouts = int(c.get("timeout", 0))
        url_err = int(c.get("url_error", 0))
        retries = int(c.get("retries", 0))
        bo = float(snap["backoff_sleep_s"])
        overall = (
            f"Overall  {_bar(done_jobs, total_jobs, 28)} {done_jobs}/{total_jobs} ({_pct(done_jobs, total_jobs):5.1f}%) "
            f"ok={counts['ok']} missing={counts['missing']} failed={counts['failed']} 429={r429} 503={r503} urlerr={url_err} timeout={timeouts} retries={retries} backoff={bo:.0f}s "
            f"elapsed={elapsed/60:.1f}m eta={_eta(elapsed, done_jobs, total_jobs)}"
        )
        lines = [overall, ""]

        for s in symbols:
            d = per_symbol_done[s]
            t = per_symbol_total[s]
            lines.append(f"{s:7s} {_bar(d, t, 22)} {d}/{t} ({_pct(d, t):5.1f}%)")

        out = "\n".join(lines)
        if args.no_ansi:
            # Snapshot mode.
            print(out)
            return

        # Clear screen + home cursor, then print dashboard.
        sys.stdout.write("\x1b[2J\x1b[H")
        sys.stdout.write(out)
        sys.stdout.write("\n")
        sys.stdout.flush()

    def progress_callback(sym: str, _date: dt.date, status: str) -> None:
        nonlocal done_jobs
        done_jobs += 1
        if sym in per_symbol_done:
            per_symbol_done[sym] += 1
        if status in counts:
            counts[status] += 1
        _render(force=False)

    def _apply_skipped_range(b_start: dt.date, b_end: dt.date, syms: Sequence[str]) -> None:
        nonlocal done_jobs
        days = (b_end - b_start).days + 1
        done_jobs += days * len(syms)
        for s in syms:
            per_symbol_done[s] += days

    if args.mode == "by-month":
        # Legacy mode: month-by-month across all symbols.
        for b_start, b_end in iter_month_batches(args.start, args.end):
            marker = marker_path(processed_root, b_start)
            if marker.exists() and not args.force:
                try:
                    payload = json.loads(marker.read_text(encoding="utf-8"))
                except Exception:
                    payload = {}
                if payload.get("complete") is True:
                    print(f"Skipping {b_start.year:04d}-{b_start.month:02d} (marker complete)")
                    _apply_skipped_range(b_start, b_end, symbols)
                    _render(force=True)
                    continue

            if args.progress:
                _render(force=True)
                print(f"Running batch {b_start.year:04d}-{b_start.month:02d} ({b_start} to {b_end})")

            stats = download_prices.run_downloads(
                symbols=symbols,
                start=b_start,
                end=b_end,
                raw_root=Path(args.raw_root),
                processed_root=Path(args.processed_root),
                workers=args.workers,
                timeout_s=args.timeout,
                sleep_s=args.sleep,
                max_retries=args.max_retries,
                base_backoff_s=args.backoff,
                max_backoff_s=args.max_backoff,
                user_agent=args.user_agent or "vanta-research/0.1 (personal research; rate-limited)",
                http_concurrency=args.http_concurrency,
                progress=False,
                progress_every=100,
                progress_callback=progress_callback,
                metrics=metrics,
                error_log_path=error_log_path,
            )

            failed_count = int(stats.get("failed", 0))
            if failed_count != 0:
                print(f"Batch had failures for {b_start.year:04d}-{b_start.month:02d} (failed={failed_count})")

            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(
                json.dumps(
                    {
                        "completed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                        "complete": failed_count == 0,
                        "start": b_start.isoformat(),
                        "end": b_end.isoformat(),
                        "symbols": symbols,
                        "raw_root": args.raw_root,
                        "processed_root": args.processed_root,
                        "failed_jobs": stats.get("failed_jobs", []),
                        "download_prices_args": {
                            "workers": args.workers,
                            "sleep": args.sleep,
                            "timeout": args.timeout,
                            "max_retries": args.max_retries,
                            "backoff": args.backoff,
                            "max_backoff": args.max_backoff,
                            "http_concurrency": args.http_concurrency,
                            "user_agent": args.user_agent,
                        },
                    },
                    indent=2,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
            print(f"Wrote marker: {marker}")

            if failed_count != 0 and not args.continue_on_error:
                return 2

            _render(force=True)
    else:
        # Preferred mode: finish priority symbols end-to-end.
        for sym in symbols:
            if args.progress:
                _render(force=True)
                print(f"Starting symbol: {sym}")
            for b_start, b_end in iter_month_batches(args.start, args.end):
                marker = marker_path_symbol(processed_root, sym, b_start)
                if marker.exists() and not args.force:
                    try:
                        payload = json.loads(marker.read_text(encoding="utf-8"))
                    except Exception:
                        payload = {}
                    if payload.get("complete") is True:
                        print(f"Skipping {sym} {b_start.year:04d}-{b_start.month:02d} (marker complete)")
                        _apply_skipped_range(b_start, b_end, [sym])
                        _render(force=True)
                        continue

                stats = download_prices.run_downloads(
                    symbols=[sym],
                    start=b_start,
                    end=b_end,
                    raw_root=Path(args.raw_root),
                    processed_root=Path(args.processed_root),
                    workers=args.workers,
                    timeout_s=args.timeout,
                    sleep_s=args.sleep,
                    max_retries=args.max_retries,
                    base_backoff_s=args.backoff,
                    max_backoff_s=args.max_backoff,
                    user_agent=args.user_agent or "vanta-research/0.1 (personal research; rate-limited)",
                    http_concurrency=args.http_concurrency,
                    progress=False,
                    progress_every=100,
                    progress_callback=progress_callback,
                    metrics=metrics,
                    error_log_path=error_log_path,
                )

                failed_count = int(stats.get("failed", 0))
                if failed_count != 0:
                    print(f"Batch had failures for {sym} {b_start.year:04d}-{b_start.month:02d} (failed={failed_count})")

                marker.parent.mkdir(parents=True, exist_ok=True)
                marker.write_text(
                    json.dumps(
                        {
                            "completed_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                            "complete": failed_count == 0,
                            "symbol": sym,
                            "start": b_start.isoformat(),
                            "end": b_end.isoformat(),
                            "raw_root": args.raw_root,
                            "processed_root": args.processed_root,
                            "failed_jobs": stats.get("failed_jobs", []),
                            "download_prices_args": {
                                "workers": args.workers,
                                "sleep": args.sleep,
                                "timeout": args.timeout,
                                "max_retries": args.max_retries,
                                "backoff": args.backoff,
                                "max_backoff": args.max_backoff,
                                "http_concurrency": args.http_concurrency,
                                "user_agent": args.user_agent,
                            },
                        },
                        indent=2,
                        sort_keys=True,
                    ),
                    encoding="utf-8",
                )
                print(f"Wrote marker: {marker}")

                _render(force=True)

                if failed_count != 0 and not args.continue_on_error:
                    return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

    error_log_path = Path(args.error_log) if args.error_log else None


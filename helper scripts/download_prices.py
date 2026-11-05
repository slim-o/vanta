"""Download and decode Dukascopy bid/ask 1-minute candles (route 1, no keys).

This script targets Phase 3 of the project overview.

Data source
- Dukascopy public historical data feed (no authentication required).

Outputs
- Raw downloads: `data/raw/price_data/dukascopy/.../*.bi5`
- Processed combined candles (bid+ask): `data/processed/price_data/dukascopy_m1/.../*.csv`

Notes
- Dukascopy minute candle files are LZMA-compressed `.bi5` files.
- Decompressed record format for `*_candles_min_1.bi5` is consecutive 24-byte
  big-endian records: `struct.unpack(">IIIIIf")` yielding
  (seconds, open, close, low, high, volume). Prices are scaled integers.
- Month in the URL path is zero-based (January is `00`).
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import lzma
import random
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple


DUKASCOPY_BASE_URL = "https://datafeed.dukascopy.com/datafeed"


RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}


MAJOR_CURRENCIES = ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF")


MAJOR_PAIRS_28 = (
    "AUDCAD",
    "AUDCHF",
    "AUDJPY",
    "AUDNZD",
    "AUDUSD",
    "CADCHF",
    "CADJPY",
    "CHFJPY",
    "EURAUD",
    "EURCAD",
    "EURCHF",
    "EURGBP",
    "EURJPY",
    "EURNZD",
    "EURUSD",
    "GBPAUD",
    "GBPCAD",
    "GBPCHF",
    "GBPJPY",
    "GBPNZD",
    "GBPUSD",
    "NZDCAD",
    "NZDCHF",
    "NZDJPY",
    "NZDUSD",
    "USDCAD",
    "USDCHF",
    "USDJPY",
)


@dataclass(frozen=True)
class Candle:
    seconds: int
    open: float
    high: float
    low: float
    close: float
    volume: float


class DownloadMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.counters: Dict[str, int] = {}
        self.backoff_sleep_s: float = 0.0

    def incr(self, key: str, amount: int = 1) -> None:
        with self._lock:
            self.counters[key] = int(self.counters.get(key, 0)) + int(amount)

    def add_backoff(self, seconds: float) -> None:
        with self._lock:
            self.backoff_sleep_s += float(seconds)

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            return {
                "counters": dict(self.counters),
                "backoff_sleep_s": float(self.backoff_sleep_s),
            }


def daterange(start: dt.date, end: dt.date) -> Iterator[dt.date]:
    current = start
    while current <= end:
        yield current
        current = current + dt.timedelta(days=1)


def month_zero_based(date: dt.date) -> str:
    return f"{date.month - 1:02d}"


def guess_price_scale(symbol: str) -> float:
    # Simple heuristic for major FX pairs.
    # JPY quotes typically use 1e3 scaling.
    if symbol.endswith("JPY"):
        return 1e3
    return 1e5


def dukascopy_m1_url(symbol: str, date: dt.date, side: str) -> str:
    side_upper = side.upper()
    if side_upper not in {"BID", "ASK"}:
        raise ValueError("side must be BID or ASK")
    return (
        f"{DUKASCOPY_BASE_URL}/{symbol}/{date.year}/{month_zero_based(date)}/{date.day:02d}/"
        f"{side_upper}_candles_min_1.bi5"
    )


def raw_bi5_path(raw_root: Path, symbol: str, date: dt.date, side: str) -> Path:
    return (
        raw_root
        / "dukascopy"
        / symbol
        / str(date.year)
        / month_zero_based(date)
        / f"{date.day:02d}"
        / f"{side.upper()}_candles_min_1.bi5"
    )


def processed_day_path(processed_root: Path, symbol: str, date: dt.date) -> Path:
    return (
        processed_root
        / "dukascopy_m1"
        / symbol
        / str(date.year)
        / f"{date.month:02d}"
        / f"{date.day:02d}.csv"
    )


def _retry_sleep_s(*, attempt: int, base_backoff_s: float, max_backoff_s: float, retry_after: Optional[str]) -> float:
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass

    exp = base_backoff_s * (2**attempt)
    jitter = random.random() * min(1.0, base_backoff_s)
    return min(max_backoff_s, exp + jitter)


def download_file_with_retry(
    url: str,
    out_path: Path,
    *,
    timeout_s: int,
    max_retries: int = 8,
    base_backoff_s: float = 1.0,
    max_backoff_s: float = 120.0,
    user_agent: str = "vanta-research/0.1 (contact: local)",
    request_gate: Optional[threading.Semaphore] = None,
    metrics: Optional[DownloadMetrics] = None,
) -> bool:
    if out_path.exists() and out_path.stat().st_size > 0:
        return True

    out_path.parent.mkdir(parents=True, exist_ok=True)
    last_error: Optional[Exception] = None
    for attempt in range(0, max_retries + 1):
        try:
            if request_gate is not None:
                request_gate.acquire()
            try:
                if metrics is not None:
                    metrics.incr("http_attempts", 1)
                req = urllib.request.Request(url, headers={"User-Agent": user_agent})
                with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                    data = resp.read()
            finally:
                if request_gate is not None:
                    request_gate.release()

            if not data:
                raise urllib.error.URLError("empty response")

            if metrics is not None:
                metrics.incr("http_ok", 1)
            break

        except urllib.error.HTTPError as e:
            if e.code == 404:
                if metrics is not None:
                    metrics.incr("http_404", 1)
                return False
            if e.code not in RETRYABLE_HTTP_CODES or attempt >= max_retries:
                if metrics is not None:
                    metrics.incr(f"http_{e.code}", 1)
                raise
            last_error = e
            if metrics is not None:
                metrics.incr(f"http_{e.code}", 1)
                metrics.incr("retries", 1)
            sleep_s = _retry_sleep_s(
                attempt=attempt,
                base_backoff_s=base_backoff_s,
                max_backoff_s=max_backoff_s,
                retry_after=e.headers.get("Retry-After"),
            )
            if metrics is not None:
                metrics.add_backoff(sleep_s)
            time.sleep(sleep_s)
            continue
        except urllib.error.URLError as e:
            if attempt >= max_retries:
                if metrics is not None:
                    metrics.incr("url_error_final", 1)
                raise
            last_error = e
            if metrics is not None:
                metrics.incr("url_error", 1)
                metrics.incr("retries", 1)
                if isinstance(getattr(e, "reason", None), socket.timeout):
                    metrics.incr("timeout", 1)
            sleep_s = _retry_sleep_s(
                attempt=attempt,
                base_backoff_s=base_backoff_s,
                max_backoff_s=max_backoff_s,
                retry_after=None,
            )
            if metrics is not None:
                metrics.add_backoff(sleep_s)
            time.sleep(sleep_s)
            continue

    else:
        # Should not happen, but keep mypy happy.
        raise last_error or RuntimeError("download failed")

    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(out_path)
    return True


def decode_bi5_candles(path: Path, *, price_scale: float) -> List[Candle]:
    compressed = path.read_bytes()
    decompressed = lzma.decompress(compressed, format=lzma.FORMAT_ALONE)
    record_size = 24
    if len(decompressed) % record_size != 0:
        # Avoid silently mis-parsing.
        raise ValueError(f"Unexpected decompressed size for {path}")

    candles: List[Candle] = []
    for offset in range(0, len(decompressed), record_size):
        seconds, open_i, close_i, low_i, high_i, volume = struct.unpack(">IIIIIf", decompressed[offset : offset + record_size])
        candles.append(
            Candle(
                seconds=int(seconds),
                open=open_i / price_scale,
                high=high_i / price_scale,
                low=low_i / price_scale,
                close=close_i / price_scale,
                volume=float(volume),
            )
        )

    return candles


def index_by_seconds(candles: Sequence[Candle]) -> Dict[int, Candle]:
    out: Dict[int, Candle] = {}
    for c in candles:
        out[c.seconds] = c
    return out


def write_combined_day_csv(
    out_path: Path,
    date: dt.date,
    bid: Sequence[Candle],
    ask: Sequence[Candle],
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    bid_map = index_by_seconds(bid)
    ask_map = index_by_seconds(ask)
    all_seconds = sorted(set(bid_map.keys()) | set(ask_map.keys()))

    day_start = dt.datetime(date.year, date.month, date.day, tzinfo=dt.timezone.utc)

    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "timestamp_utc",
                "bid_open",
                "bid_high",
                "bid_low",
                "bid_close",
                "bid_volume",
                "ask_open",
                "ask_high",
                "ask_low",
                "ask_close",
                "ask_volume",
            ]
        )

        for sec in all_seconds:
            ts = day_start + dt.timedelta(seconds=int(sec))
            b = bid_map.get(sec)
            a = ask_map.get(sec)
            writer.writerow(
                [
                    ts.isoformat(),
                    "" if b is None else b.open,
                    "" if b is None else b.high,
                    "" if b is None else b.low,
                    "" if b is None else b.close,
                    "" if b is None else b.volume,
                    "" if a is None else a.open,
                    "" if a is None else a.high,
                    "" if a is None else a.low,
                    "" if a is None else a.close,
                    "" if a is None else a.volume,
                ]
            )


def process_symbol_day(
    symbol: str,
    date: dt.date,
    raw_root: Path,
    processed_root: Path,
    *,
    timeout_s: int,
    sleep_s: float,
    request_gate: threading.Semaphore,
    max_retries: int,
    base_backoff_s: float,
    max_backoff_s: float,
    user_agent: str,
    metrics: Optional[DownloadMetrics],
) -> Tuple[str, dt.date, str]:
    bid_url = dukascopy_m1_url(symbol, date, "BID")
    ask_url = dukascopy_m1_url(symbol, date, "ASK")
    bid_path = raw_bi5_path(raw_root, symbol, date, "BID")
    ask_path = raw_bi5_path(raw_root, symbol, date, "ASK")

    bid_ok = download_file_with_retry(
        bid_url,
        bid_path,
        timeout_s=timeout_s,
        request_gate=request_gate,
        max_retries=max_retries,
        base_backoff_s=base_backoff_s,
        max_backoff_s=max_backoff_s,
        user_agent=user_agent,
        metrics=metrics,
    )
    time.sleep(sleep_s)
    ask_ok = download_file_with_retry(
        ask_url,
        ask_path,
        timeout_s=timeout_s,
        request_gate=request_gate,
        max_retries=max_retries,
        base_backoff_s=base_backoff_s,
        max_backoff_s=max_backoff_s,
        user_agent=user_agent,
        metrics=metrics,
    )

    if not bid_ok and not ask_ok:
        return symbol, date, "missing"

    scale = guess_price_scale(symbol)
    bid = decode_bi5_candles(bid_path, price_scale=scale) if bid_ok else []
    ask = decode_bi5_candles(ask_path, price_scale=scale) if ask_ok else []

    out_path = processed_day_path(processed_root, symbol, date)
    write_combined_day_csv(out_path, date, bid, ask)
    return symbol, date, "ok"


def parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download Dukascopy bid/ask M1 candles.")
    parser.add_argument(
        "--symbols",
        nargs="*",
        default=list(MAJOR_PAIRS_28),
        help="Dukascopy symbols (default: 28 major pairs)",
    )
    parser.add_argument("--start", required=True, type=parse_date, help="Start date (YYYY-MM-DD, UTC)")
    parser.add_argument("--end", required=True, type=parse_date, help="End date (YYYY-MM-DD, UTC)")
    parser.add_argument("--raw-root", default="data/raw/price_data", help="Raw data root")
    parser.add_argument("--processed-root", default="data/processed/price_data", help="Processed data root")
    parser.add_argument(
        "--workers",
        type=int,
        default=2,
        help="Download/processing worker threads (lower is safer to avoid 429/503)",
    )
    parser.add_argument("--timeout", type=int, default=60, help="HTTP timeout seconds")
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.35,
        help="Delay between bid/ask requests to reduce throttling",
    )
    parser.add_argument("--max-retries", type=int, default=8, help="Retries for 429/5xx/timeouts")
    parser.add_argument("--backoff", type=float, default=1.0, help="Base backoff seconds for retries")
    parser.add_argument("--max-backoff", type=float, default=120.0, help="Max backoff seconds")
    parser.add_argument(
        "--http-concurrency",
        type=int,
        default=1,
        help="Max concurrent HTTP requests (increase carefully; default 1 avoids 429/503)",
    )
    parser.add_argument(
        "--user-agent",
        default="vanta-research/0.1 (personal research; rate-limited)",
        help="HTTP User-Agent header",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Show a single-line progress bar (updates frequently)",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100,
        help="If not using --progress, print a progress line every N completed jobs",
    )
    parser.add_argument(
        "--error-log",
        default="",
        help="Optional JSONL file to append failed job details to",
    )
    args = parser.parse_args()

    stats = run_downloads(
        symbols=[s.upper() for s in args.symbols],
        start=args.start,
        end=args.end,
        raw_root=Path(args.raw_root),
        processed_root=Path(args.processed_root),
        workers=args.workers,
        timeout_s=args.timeout,
        sleep_s=args.sleep,
        max_retries=args.max_retries,
        base_backoff_s=args.backoff,
        max_backoff_s=args.max_backoff,
        user_agent=args.user_agent,
        http_concurrency=args.http_concurrency,
        progress=args.progress,
        progress_every=args.progress_every,
        progress_callback=None,
        metrics=None,
        error_log_path=Path(args.error_log) if args.error_log else None,
    )

    return 0 if stats["failed"] == 0 else 2


def _format_bar(done: int, total_jobs: int, width: int = 28) -> str:
    if total_jobs <= 0:
        return "[" + "." * width + "]"
    filled = int(width * done / total_jobs)
    filled = max(0, min(width, filled))
    return "[" + "#" * filled + "." * (width - filled) + "]"


def _format_eta(elapsed_s: float, done: int, total_jobs: int) -> str:
    if done <= 0:
        return "--:--"
    rate = done / max(0.001, elapsed_s)
    remaining = max(0, total_jobs - done)
    eta = remaining / max(0.0001, rate)
    mins, secs = divmod(int(eta), 60)
    hrs, mins = divmod(mins, 60)
    if hrs:
        return f"{hrs:d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def run_downloads(
    *,
    symbols: Sequence[str],
    start: dt.date,
    end: dt.date,
    raw_root: Path,
    processed_root: Path,
    workers: int,
    timeout_s: int,
    sleep_s: float,
    max_retries: int,
    base_backoff_s: float,
    max_backoff_s: float,
    user_agent: str,
    http_concurrency: int,
    progress: bool,
    progress_every: int,
    progress_callback: Optional[Callable[[str, dt.date, str], None]],
    metrics: Optional[DownloadMetrics],
    error_log_path: Optional[Path],
) -> Dict[str, int]:
    raw_root.mkdir(parents=True, exist_ok=True)
    processed_root.mkdir(parents=True, exist_ok=True)

    dates = list(daterange(start, end))
    jobs: List[Tuple[str, dt.date]] = [(sym.upper(), d) for sym in symbols for d in dates]
    total = len(jobs)

    started = time.time()
    ok = 0
    missing = 0
    failed = 0
    failed_jobs: List[Dict[str, str]] = []

    if metrics is None:
        metrics = DownloadMetrics()

    print(f"Downloading {len(symbols)} symbols x {len(dates)} days = {total} jobs")

    # Global request gate to avoid hammering the host.
    # Increase with care (Dukascopy will 429/503 if pushed too hard).
    request_gate = threading.Semaphore(max(1, int(http_concurrency)))

    if error_log_path is not None:
        error_log_path.parent.mkdir(parents=True, exist_ok=True)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        future_to_job: Dict[object, Tuple[str, dt.date]] = {}
        futures = []
        for sym, d in jobs:
            fut = ex.submit(
                process_symbol_day,
                sym,
                d,
                raw_root,
                processed_root,
                timeout_s=timeout_s,
                sleep_s=sleep_s,
                request_gate=request_gate,
                max_retries=max_retries,
                base_backoff_s=base_backoff_s,
                max_backoff_s=max_backoff_s,
                user_agent=user_agent,
                metrics=metrics,
            )
            future_to_job[fut] = (sym, d)
            futures.append(fut)

        for idx, fut in enumerate(as_completed(futures), start=1):
            sym: str
            d: dt.date
            status: str
            try:
                sym, d, status = fut.result()
            except Exception as e:  # noqa: BLE001
                failed += 1
                status = "failed"
                sym, d = future_to_job.get(fut, ("?", start))
                err = f"{type(e).__name__}: {e}"
                failed_jobs.append(
                    {
                        "symbol": sym,
                        "date": d.isoformat(),
                        "error": err,
                    }
                )
                if error_log_path is not None:
                    with error_log_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"symbol": sym, "date": d.isoformat(), "error": err}) + "\n")
                if not progress:
                    print(f"[{idx}/{total}] failed: {e}")
            else:
                if status == "ok":
                    ok += 1
                elif status == "missing":
                    missing += 1

            if progress_callback is not None:
                progress_callback(sym, d, status)

            elapsed = time.time() - started
            if progress:
                bar = _format_bar(idx, total)
                eta = _format_eta(elapsed, idx, total)
                snap = metrics.snapshot()
                c = snap["counters"]
                r429 = int(c.get("http_429", 0))
                r503 = int(c.get("http_503", 0))
                retries = int(c.get("retries", 0))
                timeouts = int(c.get("timeout", 0))
                url_err = int(c.get("url_error", 0))
                bo = float(snap["backoff_sleep_s"])
                print(
                    f"\r{bar} {idx}/{total} ok={ok} missing={missing} failed={failed} 429={r429} 503={r503} urlerr={url_err} timeout={timeouts} retries={retries} backoff={bo:.0f}s elapsed={elapsed:.1f}s eta={eta}",
                    end="",
                    flush=True,
                )
            else:
                n = max(1, int(progress_every))
                if idx % n == 0 or idx == total:
                    print(f"[{idx}/{total}] ok={ok} missing={missing} failed={failed} elapsed={elapsed:.1f}s")

        if progress:
            print("")

    return {
        "ok": ok,
        "missing": missing,
        "failed": failed,
        "total": total,
        "failed_jobs": failed_jobs,
    }


if __name__ == "__main__":
    raise SystemExit(main())


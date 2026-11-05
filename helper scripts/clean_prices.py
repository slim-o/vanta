"""Validate processed Dukascopy M1 bid/ask candles.

This is a lightweight Phase 3 quality-control step:
- Checks for duplicates.
- Checks for missing minutes within a day.
- Produces a small JSON summary report.

Input
- `data/processed/price_data/dukascopy_m1/{SYMBOL}/{YYYY}/{MM}/{DD}.csv`

Output
- `data/processed/price_data/dukascopy_m1_qc_report.json`
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


def parse_ts(value: str) -> dt.datetime:
    # Expect ISO-8601 with timezone.
    return dt.datetime.fromisoformat(value)


def iter_processed_files(root: Path) -> Iterable[Path]:
    yield from root.rglob("dukascopy_m1/*/*/*/*.csv")


def qc_file(path: Path) -> Dict[str, object]:
    timestamps: List[dt.datetime] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts_raw = (row.get("timestamp_utc") or "").strip()
            if not ts_raw:
                continue
            timestamps.append(parse_ts(ts_raw))

    if not timestamps:
        return {
            "path": str(path.as_posix()),
            "rows": 0,
            "duplicates": 0,
            "missing_minutes": None,
        }

    timestamps_sorted = sorted(timestamps)
    dup_count = len(timestamps_sorted) - len(set(timestamps_sorted))

    # Compute missing minutes only if this looks like a single UTC day file.
    first = timestamps_sorted[0]
    day_start = dt.datetime(first.year, first.month, first.day, tzinfo=dt.timezone.utc)
    expected = {day_start + dt.timedelta(minutes=i) for i in range(24 * 60)}
    observed = set(timestamps_sorted)

    missing = sorted(expected - observed)
    # Keep report small.
    missing_sample = [t.isoformat() for t in missing[:25]]
    return {
        "path": str(path.as_posix()),
        "rows": len(timestamps_sorted),
        "duplicates": dup_count,
        "missing_minutes": len(missing),
        "missing_sample": missing_sample,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="QC processed Dukascopy M1 bid/ask candles")
    parser.add_argument("--root", default="data/processed/price_data", help="Processed data root")
    parser.add_argument(
        "--out",
        default="data/processed/price_data/dukascopy_m1_qc_report.json",
        help="QC report path",
    )
    parser.add_argument("--limit", type=int, default=0, help="If non-zero, only QC first N files")
    args = parser.parse_args()

    root = Path(args.root)
    paths = list(iter_processed_files(root))
    paths.sort()
    if args.limit:
        paths = paths[: args.limit]

    reports: List[Dict[str, object]] = []
    missing_total = 0
    duplicate_total = 0

    for p in paths:
        r = qc_file(p)
        reports.append(r)
        missing_total += int(r.get("missing_minutes") or 0)
        duplicate_total += int(r.get("duplicates") or 0)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "files": len(reports),
        "missing_minutes_total": missing_total,
        "duplicate_timestamps_total": duplicate_total,
        "file_reports": reports,
    }
    out_path.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print("QC report:", out_path)
    print("Files:", len(reports))
    print("Missing minutes total:", missing_total)
    print("Duplicate timestamps total:", duplicate_total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


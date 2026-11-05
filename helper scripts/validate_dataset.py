"""Validate the event↔price reaction dataset (Phase 4 QC).

Checks:
- File exists and has expected columns.
- Basic missing-rate stats for return columns.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path
from typing import Dict, List


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate event reaction dataset")
    parser.add_argument(
        "--input",
        default="data/processed/research/event_reactions_m1.csv",
        help="Dataset CSV produced by build_event_dataset.py",
    )
    parser.add_argument("--sample", type=int, default=5, help="Print first N rows")
    args = parser.parse_args()

    path = Path(args.input)
    if not path.exists():
        raise SystemExit(f"Dataset not found: {path}")

    missing = Counter()
    total = 0
    ret_cols: List[str] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise SystemExit("CSV has no header")
        ret_cols = [c for c in reader.fieldnames if c.startswith("ret_")]
        rows: List[Dict[str, str]] = []
        for row in reader:
            total += 1
            if len(rows) < args.sample:
                rows.append(row)
            for c in ret_cols:
                if (row.get(c) or "") == "":
                    missing[c] += 1

    print("Rows:", total)
    print("Return columns:", ", ".join(ret_cols))
    for c in ret_cols:
        rate = 0.0 if total == 0 else 100.0 * missing[c] / total
        print(f"Missing {c}: {missing[c]} ({rate:.1f}%)")

    if rows:
        print("Sample rows:")
        for r in rows:
            print(
                {
                    "event_id": r.get("event_id"),
                    "scheduled_at_utc": r.get("scheduled_at_utc"),
                    "symbol": r.get("symbol"),
                    "ret_1m": r.get("ret_1m"),
                    "ret_30m": r.get("ret_30m"),
                }
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


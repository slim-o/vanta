"""Inspect the raw economic calendar dataset and produce a QC report.

Phase 1 goals (from project overview):
- Preserve raw source files unchanged (this script never edits them).
- Record source metadata (hash, row count, columns, etc.).
- Inspect schema (columns, formats, currencies, impact labels).
- Identify duplicates, missing timestamps, malformed records.
- Build a working extract filtered to 2020-01-01 onwards and high-impact events.

This script is intentionally stdlib-only so it can run before we decide on
dependencies.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T")
MAGNITUDE_RE = re.compile(r"(?i)\b\d+(?:\.\d+)?\s*[KMB]\b")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def parse_datetime(value: str) -> Tuple[Optional[dt.datetime], Optional[str]]:
    """Parse ISO-ish timestamps like `2007-01-01T04:30:00+03:30`.

    Returns (aware_datetime, error_string).
    """

    raw = (value or "").strip()
    if not raw:
        return None, "missing"

    try:
        # Handles timezone offsets like +03:30 on Python 3.10.
        parsed = dt.datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            return None, "naive_timestamp"
        return parsed, None
    except ValueError:
        pass

    # Very small fallback set; we can extend once we see real failures.
    candidates = [
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in candidates:
        try:
            parsed = dt.datetime.strptime(raw, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed, "fallback_format"
        except ValueError:
            continue

    return None, "unparseable"


def iter_csv_rows(path: Path) -> Iterable[Tuple[int, Dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row_index, row in enumerate(reader, start=2):
            # row_index is 1-based lines; header line = 1.
            yield row_index, {k: (v if v is not None else "") for k, v in row.items()}


def normalise_utc(parsed: dt.datetime) -> dt.datetime:
    return parsed.astimezone(dt.timezone.utc)


def build_filtered_extract(
    rows: List[Dict[str, Any]],
    out_path: Path,
    fieldnames: List[str],
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect the raw economic calendar CSV(s).")
    parser.add_argument(
        "--raw-dir",
        default="data/raw/economic_calendar",
        help="Directory containing raw economic calendar files",
    )
    parser.add_argument(
        "--processed-dir",
        default="data/processed/economic_events",
        help="Directory for derived (working) datasets and QC reports",
    )
    parser.add_argument(
        "--min-date",
        default="2020-01-01",
        help="Minimum UTC date for the working extract (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--impact-prefix",
        default="High",
        help="Keep events whose Impact starts with this prefix (case-insensitive)",
    )
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    processed_dir = Path(args.processed_dir)

    if not raw_dir.exists():
        raise SystemExit(f"Raw dir not found: {raw_dir}")

    min_dt = dt.datetime.fromisoformat(args.min_date).replace(tzinfo=dt.timezone.utc)
    impact_prefix = args.impact_prefix.strip().lower()

    csv_paths = sorted(raw_dir.glob("*.csv"))
    if not csv_paths:
        raise SystemExit(f"No .csv files found in {raw_dir}")

    manifest: Dict[str, Any] = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "raw_dir": str(raw_dir.as_posix()),
        "files": [],
        # Optional fields you can fill in manually once confirmed:
        "source_url": "",
        "source_version": "",
        "license": "",
        "notes": "",
    }

    overall = {
        "total_rows": 0,
        "currencies": Counter(),
        "impacts": Counter(),
        "date_parse_errors": Counter(),
        "missing_fields": Counter(),
        "duplicates": 0,
        "duplicate_examples": [],
        "value_patterns": Counter(),
        "value_nonempty": Counter(),
        "value_examples": {
            "percent": [],
            "magnitude_suffix": [],
            "thousands_sep": [],
            "other_non_numeric": [],
        },
    }

    # Small enough (83k rows) to store keys in-memory.
    seen_keys: Dict[Tuple[str, str, str], int] = {}
    filtered_rows: List[Dict[str, Any]] = []

    required_columns = ["DateTime", "Currency", "Impact", "Event", "Actual", "Forecast", "Previous", "Detail"]

    for csv_path in csv_paths:
        file_hash = sha256_file(csv_path)

        # Read header for column inspection.
        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            header = next(reader, [])

        header_set = set(header)
        missing_cols = [c for c in required_columns if c not in header_set]
        extra_cols = [c for c in header if c not in required_columns]

        file_report: Dict[str, Any] = {
            "path": str(csv_path.as_posix()),
            "filename": csv_path.name,
            "bytes": csv_path.stat().st_size,
            "sha256": file_hash,
            "header": header,
            "missing_required_columns": missing_cols,
            "extra_columns": extra_cols,
            "rows": 0,
        }

        for row_index, row in iter_csv_rows(csv_path):
            file_report["rows"] += 1
            overall["total_rows"] += 1

            # Basic required-field checks.
            for col in ("DateTime", "Currency", "Impact", "Event"):
                if not (row.get(col, "").strip()):
                    overall["missing_fields"][col] += 1

            currency = row.get("Currency", "").strip()
            impact = row.get("Impact", "").strip()
            event = row.get("Event", "").strip()
            overall["currencies"][currency] += 1
            overall["impacts"][impact] += 1

            parsed_dt, parse_err = parse_datetime(row.get("DateTime", ""))
            if parse_err:
                overall["date_parse_errors"][parse_err] += 1
                continue

            assert parsed_dt is not None
            utc_dt = normalise_utc(parsed_dt)

            # Duplicate detection on exact raw DateTime + Currency + Event.
            dup_key = (row.get("DateTime", "").strip(), currency, event)
            if dup_key in seen_keys:
                overall["duplicates"] += 1
                if len(overall["duplicate_examples"]) < 25:
                    overall["duplicate_examples"].append(
                        {
                            "key": {"DateTime": dup_key[0], "Currency": dup_key[1], "Event": dup_key[2]},
                            "first_seen_line": seen_keys[dup_key],
                            "duplicate_line": row_index,
                            "file": csv_path.name,
                        }
                    )
            else:
                seen_keys[dup_key] = row_index

            # Lightweight value-pattern scan (helps plan normalisation later).
            for col in ("Actual", "Forecast", "Previous"):
                val = (row.get(col, "") or "").strip()
                if not val:
                    continue

                overall["value_nonempty"][col] += 1

                def _maybe_add_example(bucket: str) -> None:
                    examples: List[str] = overall["value_examples"][bucket]
                    if len(examples) >= 25:
                        return
                    if val not in examples:
                        examples.append(val)

                if val.endswith("%"):
                    overall["value_patterns"]["percent"] += 1
                    _maybe_add_example("percent")
                if MAGNITUDE_RE.search(val):
                    overall["value_patterns"]["magnitude_suffix"] += 1
                    _maybe_add_example("magnitude_suffix")
                if any(ch in val for ch in (",", " ")) and val.replace(",", "").replace(" ", "").replace(".", "").isdigit():
                    overall["value_patterns"]["thousands_sep"] += 1
                    _maybe_add_example("thousands_sep")
                # Capture obvious non-numeric placeholders.
                if not any(ch.isdigit() for ch in val):
                    overall["value_patterns"]["other_non_numeric"] += 1
                    _maybe_add_example("other_non_numeric")

            # Build working extract: 2020+ (UTC) and High-impact.
            if utc_dt >= min_dt and impact.lower().startswith(impact_prefix):
                filtered_rows.append(
                    {
                        "DateTime": row.get("DateTime", "").strip(),
                        "DateTimeUTC": utc_dt.isoformat(),
                        "Currency": currency,
                        "Impact": impact,
                        "Event": event,
                        "Actual": row.get("Actual", "").strip(),
                        "Forecast": row.get("Forecast", "").strip(),
                        "Previous": row.get("Previous", "").strip(),
                        "Detail": row.get("Detail", "").strip(),
                        "SourceFile": csv_path.name,
                        "SourceLine": row_index,
                    }
                )

        manifest["files"].append(file_report)

    # Emit artifacts
    raw_manifest_path = raw_dir / "source_manifest.json"
    raw_manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    report = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "min_date_utc": min_dt.date().isoformat(),
        "impact_prefix": args.impact_prefix,
        "total_rows": overall["total_rows"],
        "unique_currencies": len([c for c in overall["currencies"] if c]),
        "currencies_top": overall["currencies"].most_common(50),
        "impact_values": overall["impacts"].most_common(),
        "date_parse_errors": overall["date_parse_errors"].most_common(),
        "missing_fields": overall["missing_fields"].most_common(),
        "duplicate_rows": overall["duplicates"],
        "duplicate_examples": overall["duplicate_examples"],
        "value_patterns": overall["value_patterns"].most_common(),
        "value_nonempty": overall["value_nonempty"].most_common(),
        "value_examples": overall["value_examples"],
        "filtered_rows": len(filtered_rows),
        "outputs": {
            "raw_manifest": str(raw_manifest_path.as_posix()),
            "filtered_extract": str((processed_dir / "economic_calendar_2020_high_impact.csv").as_posix()),
            "qc_report": str((processed_dir / "calendar_qc_report.json").as_posix()),
        },
    }

    processed_dir.mkdir(parents=True, exist_ok=True)
    (processed_dir / "calendar_qc_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )

    build_filtered_extract(
        rows=filtered_rows,
        out_path=processed_dir / "economic_calendar_2020_high_impact.csv",
        fieldnames=[
            "DateTime",
            "DateTimeUTC",
            "Currency",
            "Impact",
            "Event",
            "Actual",
            "Forecast",
            "Previous",
            "Detail",
            "SourceFile",
            "SourceLine",
        ],
    )

    print("Raw manifest:", raw_manifest_path)
    print("QC report:", processed_dir / "calendar_qc_report.json")
    print("Filtered extract:", processed_dir / "economic_calendar_2020_high_impact.csv")
    print("Filtered rows:", len(filtered_rows))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


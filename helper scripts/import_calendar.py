"""Import the economic calendar working extract into SQLite.

Phase 2 goals (from project overview):
- Set up SQLite for economic events, source records and observations.
- Preserve provider/source provenance.
- Preserve original numeric strings (e.g. 65K, 7.1M, 3.75%).
- Add normalised numeric values and units where possible.
- Support multiple observations of the same event over time.

This script targets the Phase 1 working extract:
  data/processed/economic_events/economic_calendar_2020_high_impact.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


MAGNITUDE_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*([KMB])\s*$", re.IGNORECASE)


def now_utc_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def parse_numeric(raw: str) -> Tuple[Optional[float], Optional[str], str]:
    """Best-effort parse for values like `3.75%`, `7.1M`, `129K`, `1,234.5`.

    Returns (value, unit, status).
    """

    text = (raw or "").strip()
    if not text:
        return None, None, "empty"

    # Percent.
    if text.endswith("%"):
        inner = text[:-1].strip()
        try:
            return float(inner.replace(",", "")), "%", "parsed"
        except ValueError:
            return None, "%", "unparsed"

    # Magnitude suffix.
    match = MAGNITUDE_RE.match(text)
    if match:
        number = float(match.group(1))
        suffix = match.group(2).upper()
        multiplier = {"K": 1_000.0, "M": 1_000_000.0, "B": 1_000_000_000.0}[suffix]
        return number * multiplier, suffix, "parsed"

    # Plain numeric with separators.
    cleaned = text.replace(",", "").replace(" ", "")
    try:
        return float(cleaned), None, "parsed"
    except ValueError:
        # Some values are categorical (Yes/No/Pass/etc.)
        if not any(ch.isdigit() for ch in text):
            return None, None, "non_numeric"
        return None, None, "unparsed"


def ensure_schema(conn: sqlite3.Connection, schema_path: Path) -> None:
    schema_sql = schema_path.read_text(encoding="utf-8")
    conn.executescript(schema_sql)


def iter_csv(path: Path) -> Iterable[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yield {k: (v if v is not None else "") for k, v in row.items()}


def get_or_create_event(
    conn: sqlite3.Connection,
    scheduled_at_utc: str,
    scheduled_at_source: str,
    currency: str,
    impact: str,
    event_name: str,
    detail: str,
    created_at_utc: str,
) -> int:
    conn.execute(
        """
        INSERT OR IGNORE INTO events(
          scheduled_at_utc,
          scheduled_at_source,
          currency,
          impact,
          event_name,
          detail,
          created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (scheduled_at_utc, scheduled_at_source, currency, impact, event_name, detail, created_at_utc),
    )
    (event_id,) = conn.execute(
        """
        SELECT id
        FROM events
        WHERE scheduled_at_source = ? AND currency = ? AND event_name = ?
        """,
        (scheduled_at_source, currency, event_name),
    ).fetchone()
    return int(event_id)


def main() -> int:
    parser = argparse.ArgumentParser(description="Import the economic calendar extract into SQLite.")
    parser.add_argument(
        "--input",
        default="data/processed/economic_events/economic_calendar_2020_high_impact.csv",
        help="Phase-1 working extract CSV",
    )
    parser.add_argument(
        "--db",
        default="data/vanta.sqlite",
        help="SQLite database path",
    )
    parser.add_argument(
        "--schema",
        default="database/schema.sql",
        help="SQLite schema file",
    )
    parser.add_argument(
        "--exclude-currency",
        action="append",
        default=["CNY", "All"],
        help="Currency code to exclude (repeatable). Defaults exclude CNY and All.",
    )
    parser.add_argument(
        "--provider",
        default="forex_factory",
        help="Provider label to store in event_sources",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    db_path = Path(args.db)
    schema_path = Path(args.schema)

    if not input_path.exists():
        raise SystemExit(f"Input CSV not found: {input_path}")
    if not schema_path.exists():
        raise SystemExit(f"Schema file not found: {schema_path}")

    excluded = {c.strip() for c in args.exclude_currency if c.strip()}

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    ensure_schema(conn, schema_path)

    started_at = now_utc_iso()
    run_params = {
        "input": str(input_path.as_posix()),
        "excluded_currencies": sorted(excluded),
        "provider": args.provider,
    }

    source_sha = sha256_file(input_path)
    (run_id,) = conn.execute(
        """
        INSERT INTO collection_runs(
          started_at_utc,
          source_name,
          source_path,
          source_sha256,
          params_json
        ) VALUES (?, ?, ?, ?, ?)
        RETURNING id
        """,
        (started_at, "economic_calendar", str(input_path.as_posix()), source_sha, json.dumps(run_params, sort_keys=True)),
    ).fetchone()
    run_id = int(run_id)

    created_at = now_utc_iso()
    as_of_utc = created_at

    imported = 0
    skipped = 0
    duplicate_observations = 0
    duplicate_examples: List[Dict[str, str]] = []
    parse_status_counts = {
        "actual": {},
        "forecast": {},
        "previous": {},
    }

    def _bump_status(which: str, status: str) -> None:
        bucket = parse_status_counts[which]
        bucket[status] = int(bucket.get(status, 0)) + 1
    with conn:
        for row in iter_csv(input_path):
            currency = row.get("Currency", "").strip()
            if currency in excluded:
                skipped += 1
                continue

            event_id = get_or_create_event(
                conn=conn,
                scheduled_at_utc=row.get("DateTimeUTC", "").strip(),
                scheduled_at_source=row.get("DateTime", "").strip(),
                currency=currency,
                impact=row.get("Impact", "").strip(),
                event_name=row.get("Event", "").strip(),
                detail=row.get("Detail", "").strip(),
                created_at_utc=created_at,
            )

            raw_row_json = json.dumps(row, ensure_ascii=False, sort_keys=True)
            conn.execute(
                """
                INSERT OR IGNORE INTO event_sources(
                  event_id,
                  collected_run_id,
                  provider,
                  source_file,
                  source_line,
                  raw_row_json,
                  created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    run_id,
                    args.provider,
                    row.get("SourceFile", "").strip(),
                    int(row.get("SourceLine", "0") or 0),
                    raw_row_json,
                    created_at,
                ),
            )

            actual_raw = row.get("Actual", "").strip()
            forecast_raw = row.get("Forecast", "").strip()
            previous_raw = row.get("Previous", "").strip()

            actual_val, actual_unit, actual_status = parse_numeric(actual_raw)
            forecast_val, forecast_unit, forecast_status = parse_numeric(forecast_raw)
            previous_val, previous_unit, previous_status = parse_numeric(previous_raw)

            _bump_status("actual", actual_status)
            _bump_status("forecast", forecast_status)
            _bump_status("previous", previous_status)

            try:
                conn.execute(
                    """
                    INSERT INTO event_observations(
                      event_id,
                      collected_run_id,
                      as_of_utc,
                      actual_raw,
                      forecast_raw,
                      previous_raw,
                      actual_value,
                      forecast_value,
                      previous_value,
                      actual_unit,
                      forecast_unit,
                      previous_unit,
                      actual_parse_status,
                      forecast_parse_status,
                      previous_parse_status,
                      created_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        run_id,
                        as_of_utc,
                        actual_raw,
                        forecast_raw,
                        previous_raw,
                        actual_val,
                        forecast_val,
                        previous_val,
                        actual_unit,
                        forecast_unit,
                        previous_unit,
                        actual_status,
                        forecast_status,
                        previous_status,
                        created_at,
                    ),
                )
                imported += 1
            except sqlite3.IntegrityError:
                # Deterministic de-duplication: first row wins (file order).
                duplicate_observations += 1
                if len(duplicate_examples) < 25:
                    duplicate_examples.append(
                        {
                            "DateTime": row.get("DateTime", "").strip(),
                            "Currency": currency,
                            "Event": row.get("Event", "").strip(),
                            "SourceFile": row.get("SourceFile", "").strip(),
                            "SourceLine": row.get("SourceLine", "").strip(),
                        }
                    )

        finished_at = now_utc_iso()
        notes = {
            "skipped_currency_rows": skipped,
            "duplicate_observations_skipped": duplicate_observations,
            "duplicate_examples": duplicate_examples,
            "parse_status_counts": parse_status_counts,
        }
        conn.execute(
            """
            UPDATE collection_runs
            SET finished_at_utc = ?, row_count = ?, notes = ?
            WHERE id = ?
            """,
            (finished_at, imported, json.dumps(notes, sort_keys=True), run_id),
        )

    print("DB:", db_path)
    print("Run id:", run_id)
    print("Imported rows:", imported)
    print("Skipped rows:", skipped)
    print("Duplicate observations skipped:", duplicate_observations)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


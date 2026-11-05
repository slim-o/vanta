"""Quick sanity checks for the SQLite database.

This is intentionally stdlib-only so it can run in a fresh environment.
"""

from __future__ import annotations

import argparse
import sqlite3


def main() -> int:
    parser = argparse.ArgumentParser(description="Query basic stats from the VANTA SQLite DB.")
    parser.add_argument("--db", default="data/vanta.sqlite", help="SQLite database path")
    parser.add_argument("--top", type=int, default=15, help="Top-N for breakdowns")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    tables = [r[0] for r in cur.execute("select name from sqlite_master where type='table' order by name").fetchall()]
    print("DB:", args.db)
    print("Tables:", ", ".join(tables))

    def scalar(sql: str, params: tuple = ()) -> int:
        return int(cur.execute(sql, params).fetchone()[0])

    for name in ("collection_runs", "events", "event_sources", "event_observations"):
        if name in tables:
            print(f"{name}:", scalar(f"select count(*) from {name}"))

    if "collection_runs" in tables:
        latest = cur.execute(
            """
            select id, started_at_utc, finished_at_utc, source_name, source_path, row_count
            from collection_runs
            order by id desc
            limit 1
            """
        ).fetchone()
        if latest is not None:
            print(
                "Latest run:",
                dict(latest),
            )

    if "events" in tables:
        print("Top currencies:")
        rows = cur.execute(
            """
            select currency, count(*) as c
            from events
            group by currency
            order by c desc
            limit ?
            """,
            (args.top,),
        ).fetchall()
        for r in rows:
            print(f"  {r['currency']}: {r['c']}")

        print("Top event names:")
        rows = cur.execute(
            """
            select event_name, count(*) as c
            from events
            group by event_name
            order by c desc
            limit ?
            """,
            (args.top,),
        ).fetchall()
        for r in rows:
            print(f"  {r['event_name']}: {r['c']}")

    if "event_observations" in tables:
        print("Observation parse statuses (Actual/Forecast/Previous):")
        for col in ("actual_parse_status", "forecast_parse_status", "previous_parse_status"):
            rows = cur.execute(
                f"""
                select {col} as status, count(*) as c
                from event_observations
                group by {col}
                order by c desc
                """
            ).fetchall()
            parts = [f"{(r['status'] or 'NULL')}={r['c']}" for r in rows]
            print(f"  {col}: " + ", ".join(parts))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


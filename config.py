from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent

DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"

RAW_ECONOMIC_CALENDAR_DIR = RAW_DATA_DIR / "economic_calendar"
PROCESSED_ECONOMIC_EVENTS_DIR = PROCESSED_DATA_DIR / "economic_events"

SQLITE_DB_PATH = DATA_DIR / "vanta.sqlite"
DUCKDB_PATH = DATA_DIR / "research.duckdb"

# Project overview: start with the 8 major currencies and exclude CNY for now.
# (We also exclude the pseudo-currency bucket "All" by default.)
DEFAULT_EXCLUDED_CALENDAR_CURRENCIES = {"CNY", "All"}


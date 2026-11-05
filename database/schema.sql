PRAGMA foreign_keys = ON;

-- Collection runs track every ingestion/import process.
CREATE TABLE IF NOT EXISTS collection_runs (
  id INTEGER PRIMARY KEY,
  started_at_utc TEXT NOT NULL,
  finished_at_utc TEXT,
  source_name TEXT NOT NULL,
  source_path TEXT NOT NULL,
  source_sha256 TEXT,
  params_json TEXT NOT NULL,
  row_count INTEGER,
  notes TEXT
);

-- Events represent the scheduled release itself.
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  scheduled_at_utc TEXT NOT NULL,
  scheduled_at_source TEXT NOT NULL,
  currency TEXT NOT NULL,
  impact TEXT,
  event_name TEXT NOT NULL,
  detail TEXT,
  created_at_utc TEXT NOT NULL,
  UNIQUE (scheduled_at_source, currency, event_name)
);

CREATE INDEX IF NOT EXISTS idx_events_currency_time
  ON events(currency, scheduled_at_utc);

-- Provider-specific/source-level records for provenance.
CREATE TABLE IF NOT EXISTS event_sources (
  id INTEGER PRIMARY KEY,
  event_id INTEGER NOT NULL REFERENCES events(id),
  collected_run_id INTEGER NOT NULL REFERENCES collection_runs(id),
  provider TEXT NOT NULL,
  source_file TEXT NOT NULL,
  source_line INTEGER,
  raw_row_json TEXT,
  created_at_utc TEXT NOT NULL,
  UNIQUE (collected_run_id, source_file, source_line)
);

CREATE INDEX IF NOT EXISTS idx_event_sources_event
  ON event_sources(event_id);

-- Observations are snapshots of values (forecast/previous/actual).
-- We preserve raw strings and store best-effort normalised numeric values.
CREATE TABLE IF NOT EXISTS event_observations (
  id INTEGER PRIMARY KEY,
  event_id INTEGER NOT NULL REFERENCES events(id),
  collected_run_id INTEGER NOT NULL REFERENCES collection_runs(id),
  as_of_utc TEXT NOT NULL,

  actual_raw TEXT,
  forecast_raw TEXT,
  previous_raw TEXT,

  actual_value REAL,
  forecast_value REAL,
  previous_value REAL,

  actual_unit TEXT,
  forecast_unit TEXT,
  previous_unit TEXT,

  actual_parse_status TEXT,
  forecast_parse_status TEXT,
  previous_parse_status TEXT,

  created_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_event_observations_event
  ON event_observations(event_id);

CREATE INDEX IF NOT EXISTS idx_event_observations_asof
  ON event_observations(as_of_utc);

-- Prevent accidental duplicate inserts for the same run snapshot.
CREATE UNIQUE INDEX IF NOT EXISTS uq_event_observations_event_run_asof
  ON event_observations(event_id, collected_run_id, as_of_utc);


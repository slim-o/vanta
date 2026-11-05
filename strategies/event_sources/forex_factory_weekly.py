"""Forex Factory (Fair Economy) weekly export fetcher.

We use the public weekly JSON export ("this week") rather than scraping the
calendar HTML. The weekly export does not include actual released values.

Important: review Forex Factory terms/notices before building production or
redistributed services. For personal research/paper trading, this provides a
simple schedule feed.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.request
from dataclasses import dataclass
from typing import Iterable, List, Optional


THISWEEK_JSON_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"


@dataclass(frozen=True)
class ForexFactoryEvent:
    title: str
    country: str  # currency code or "All"
    scheduled_at_utc: dt.datetime
    impact: str  # High/Medium/Low/Holiday (per export)
    forecast: str
    previous: str


def _parse_datetime(value: str) -> dt.datetime:
    # Export date includes timezone offset (usually US Eastern).
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def fetch_thisweek_events(url: str = THISWEEK_JSON_URL, *, timeout_s: int = 30) -> List[ForexFactoryEvent]:
    req = urllib.request.Request(url, headers={"User-Agent": "vanta-research/0.1"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        raw = resp.read()
    payload = json.loads(raw)

    events: List[ForexFactoryEvent] = []
    for row in payload:
        events.append(
            ForexFactoryEvent(
                title=str(row.get("title", "")).strip(),
                country=str(row.get("country", "")).strip(),
                scheduled_at_utc=_parse_datetime(str(row.get("date", ""))),
                impact=str(row.get("impact", "")).strip(),
                forecast=str(row.get("forecast", "")).strip(),
                previous=str(row.get("previous", "")).strip(),
            )
        )
    return events


def filter_events(
    events: Iterable[ForexFactoryEvent],
    *,
    currencies: Optional[set[str]] = None,
    impacts: Optional[set[str]] = None,
    start_utc: Optional[dt.datetime] = None,
    end_utc: Optional[dt.datetime] = None,
) -> List[ForexFactoryEvent]:
    out: List[ForexFactoryEvent] = []
    for e in events:
        if currencies is not None and e.country.upper() not in currencies:
            continue
        if impacts is not None and e.impact.capitalize() not in impacts and e.impact.upper() not in impacts:
            # Keep simple; caller can pass {"High"}.
            continue
        if start_utc is not None and e.scheduled_at_utc < start_utc:
            continue
        if end_utc is not None and e.scheduled_at_utc > end_utc:
            continue
        out.append(e)
    out.sort(key=lambda x: x.scheduled_at_utc)
    return out


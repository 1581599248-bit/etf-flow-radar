"""Resolve the official-share publication target independently of intraday feeds.

The primary ETF metric is based on exchange closing shares, so its schedule must
never wait for an optional second-market snapshot.  This module intentionally
uses only China Standard Time and an explicit operator override.  The official
SSE/SZSE probe remains the final trading-day/availability authority.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


BEIJING = ZoneInfo("Asia/Shanghai")


def latest_weekday(day: date) -> date:
    while day.weekday() > 4:
        day -= timedelta(days=1)
    return day


def resolve_target(requested: str | None = None, now: datetime | None = None) -> date:
    """Return the requested day or the current/last weekday in Beijing time.

    A Chinese-market holiday is deliberately not guessed here.  The next stage
    probes both exchanges for the exact date and publishes only when both have
    complete official rows, which avoids treating a calendar assumption as data.
    """
    if requested and requested.strip():
        return date.fromisoformat(requested.strip())
    observed = now or datetime.now(BEIJING)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=BEIJING)
    return latest_weekday(observed.astimezone(BEIJING).date())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="optional YYYY-MM-DD operator override")
    args = parser.parse_args()
    print(resolve_target(args.date).isoformat())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Keep an already-started runner checking an exact official trading date.

The morning/afternoon and overnight phases have separate runner time budgets.
Only exit 75 (upstream unavailable/timeout) is retried; a failed quality gate
stops immediately. The report builder is never run inside this polling loop.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import date, datetime, time as clock_time, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo


BEIJING = ZoneInfo("Asia/Shanghai")
RETRYABLE_EXIT = 75
MAX_WATCH_SECONDS = 330 * 60  # Leave setup/cleanup headroom below the 6h job limit.


def watch_deadline(day: date, until: str, day_offset: int, now: datetime) -> datetime:
    cutoff = datetime.combine(day + timedelta(days=day_offset), clock_time.fromisoformat(until), BEIJING)
    return min(cutoff, now + timedelta(seconds=MAX_WATCH_SECONDS))


def wait_for_shares(
    day: date,
    deadline: datetime,
    interval: int,
    probe: Callable[[date], int],
    *,
    now: Callable[[], datetime] = lambda: datetime.now(BEIJING),
    sleep: Callable[[float], None] = time.sleep,
    report: Callable[[dict], None] = lambda payload: print(json.dumps(payload), flush=True),
) -> int:
    if interval <= 0:
        raise ValueError("poll interval must be positive")
    attempt = 0
    while True:
        attempt += 1
        code = probe(day)
        observed = now()
        remaining = (deadline - observed).total_seconds()
        next_check = observed + timedelta(seconds=min(interval, max(0, remaining)))
        report({
            "tradeDate": day.isoformat(),
            "checkedAt": observed.isoformat(),
            "attempt": attempt,
            "exitCode": code,
            "deadline": deadline.isoformat(),
            "nextCheckAt": next_check.isoformat() if code == RETRYABLE_EXIT and remaining > 0 else None,
        })
        if code != RETRYABLE_EXIT:
            return code
        if remaining <= 0:
            return RETRYABLE_EXIT
        sleep(min(interval, remaining))


def run_probe(day: date, status_file: Path, *, runner=subprocess.run) -> int:
    # A killed subprocess must not leave a previous ready result looking current.
    status_file.unlink(missing_ok=True)
    command = [sys.executable, str(Path(__file__).with_name("probe_official_shares.py")),
               "--date", day.isoformat(), "--status-file", str(status_file)]
    try:
        return runner(command, timeout=240, check=False).returncode
    except subprocess.TimeoutExpired:
        status_file.write_text(json.dumps({
            "tradeDate": day.isoformat(), "status": "retryable", "category": "timeout",
            "message": "Official probe exceeded 240 seconds; retry on this runner.",
        }), "utf-8")
        return RETRYABLE_EXIT


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True)
    parser.add_argument("--until", required=True, help="Beijing HH:MM cutoff")
    parser.add_argument("--day-offset", type=int, choices=(0, 1), required=True)
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--status-file", type=Path, required=True)
    args = parser.parse_args()
    day = date.fromisoformat(args.date)
    deadline = watch_deadline(day, args.until, args.day_offset, datetime.now(BEIJING))
    args.status_file.parent.mkdir(parents=True, exist_ok=True)
    print(f"Watching official SSE/SZSE date={day}; cutoff={deadline.isoformat()}", flush=True)
    return wait_for_shares(day, deadline, args.interval_seconds,
                           lambda target: run_probe(target, args.status_file))


if __name__ == "__main__":
    raise SystemExit(main())

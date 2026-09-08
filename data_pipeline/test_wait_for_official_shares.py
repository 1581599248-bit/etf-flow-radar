import json
import subprocess
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

from data_pipeline.wait_for_official_shares import (
    BEIJING, RETRYABLE_EXIT, run_probe, wait_for_shares, watch_deadline,
)


class OfficialShareWatcherTests(unittest.TestCase):
    def simulate(self, *, start, cutoff, results, interval=300, probe_seconds=0):
        clock = [datetime.fromisoformat(start).replace(tzinfo=BEIJING)]
        target = date(2026, 9, 7)
        calls, sleeps, reports = [], [], []
        remaining_results = iter(results)

        def probe(day):
            calls.append(day)
            clock[0] += timedelta(seconds=probe_seconds)
            return next(remaining_results)

        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += timedelta(seconds=seconds)

        code = wait_for_shares(target, datetime.fromisoformat(cutoff).replace(tzinfo=BEIJING),
                               interval, probe, now=lambda: clock[0], sleep=sleep, report=reports.append)
        return code, clock[0], calls, sleeps, reports

    def test_early_watch_hands_off_without_needing_another_cron(self):
        result = self.simulate(start="2026-09-07T19:45", cutoff="2026-09-07T20:00",
                               results=[75, 75], interval=900)
        self.assertEqual(result[0], RETRYABLE_EXIT)
        self.assertEqual(result[3], [900])
        self.assertEqual(result[1].hour, 20)

    def test_evening_runner_publishes_as_soon_as_official_data_passes(self):
        result = self.simulate(start="2026-09-07T23:40", cutoff="2026-09-08T01:30",
                               results=[75, 75, 0])
        self.assertEqual(result[0], 0)
        self.assertEqual(result[1].strftime("%H:%M"), "23:50")
        self.assertEqual(result[3], [300, 300])
        self.assertIsNone(result[4][-1]["nextCheckAt"])

    def test_midnight_does_not_change_the_requested_trade_date(self):
        result = self.simulate(start="2026-09-07T23:58", cutoff="2026-09-08T01:30",
                               results=[75, 0])
        self.assertEqual(result[0], 0)
        self.assertEqual(result[1].date(), date(2026, 9, 8))
        self.assertEqual(result[2], [date(2026, 9, 7)] * 2)

    def test_bad_quality_or_unexpected_process_failure_never_retries(self):
        for failure in (2, 1, -9):
            with self.subTest(failure=failure):
                result = self.simulate(start="2026-09-07T21:00", cutoff="2026-09-08T01:30", results=[failure])
                self.assertEqual(result[0], failure)
                self.assertEqual(result[3], [])

    def test_unavailable_upstream_has_a_finite_deadline(self):
        result = self.simulate(start="2026-09-08T01:29", cutoff="2026-09-08T01:30", results=[75, 75])
        self.assertEqual(result[0], 75)
        self.assertEqual(result[3], [60])
        self.assertIsNone(result[4][-1]["nextCheckAt"])

    def test_delayed_morning_or_weekend_run_still_checks_once(self):
        for code in (0, 75):
            result = self.simulate(start="2026-09-08T08:20", cutoff="2026-09-08T01:30", results=[code])
            self.assertEqual(result[0], code)
            self.assertEqual(len(result[2]), 1)
            self.assertEqual(result[3], [])

    def test_timeout_counts_toward_deadline(self):
        result = self.simulate(start="2026-09-08T01:29", cutoff="2026-09-08T01:30",
                               results=[75], probe_seconds=240)
        self.assertEqual(result[3], [])
        self.assertEqual(len(result[2]), 1)

    def test_job_budget_and_beijing_cutoff_are_bounded(self):
        start = datetime(2026, 9, 7, 20, 0, tzinfo=BEIJING)
        self.assertEqual(watch_deadline(start.date(), "01:30", 1, start), start + timedelta(hours=5, minutes=30))
        early = start.replace(hour=15)
        self.assertEqual(watch_deadline(early.date(), "20:00", 0, early), start)
        future_date = early.date() + timedelta(days=7)
        self.assertEqual((watch_deadline(future_date, "01:30", 1, early) - early).total_seconds(), 330 * 60)

    def test_subprocess_timeout_replaces_stale_success_and_is_retryable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "status.json"
            path.write_text('{"status":"ready"}')

            def runner(command, **kwargs):
                self.assertFalse(path.exists())
                self.assertIn("2026-09-07", command)
                self.assertEqual(kwargs["timeout"], 240)
                raise subprocess.TimeoutExpired(command, 240)

            self.assertEqual(run_probe(date(2026, 9, 7), path, runner=runner), 75)
            self.assertEqual(json.loads(path.read_text())["status"], "retryable")


if __name__ == "__main__":
    unittest.main()

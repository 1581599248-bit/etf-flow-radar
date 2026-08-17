import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PostCloseWorkflowTests(unittest.TestCase):
    def test_postclose_capture_retries_and_attempts_full_publish(self):
        text = (ROOT / ".github" / "workflows" / "capture-etf-order-flow.yml").read_text("utf-8")
        for cron in (
            '"35 7 * * 1-5"',
            '"5 8 * * 1-5"',
            '"35 8 * * 1-5"',
            '"30 9 * * 1-5"',
            '"30 10 * * 1-5"',
            '"0 12 * * 1-5"',
        ):
            self.assertIn(cron, text)
        self.assertIn("Resolve latest captured trade date", text)
        self.assertIn("Attempt full report as soon as end-of-day data is ready", text)
        self.assertIn('python data_pipeline/update_daily_v2.py --date "${{ steps.trade.outputs.trade_date }}"', text)
        self.assertIn("continue-on-error: true", text)
        self.assertIn("Commit verified full report immediately", text)

    def test_morning_fallback_is_still_present(self):
        text = (ROOT / ".github" / "workflows" / "daily-etf-data.yml").read_text("utf-8")
        for cron in (
            '"0 21 * * 1-5"',
            '"0 22 * * 1-5"',
            '"0 23 * * 1-5"',
            '"0 0 * * 2-6"',
        ):
            self.assertIn(cron, text)
        self.assertIn("python data_pipeline/update_daily_v2.py", text)


if __name__ == "__main__":
    unittest.main()

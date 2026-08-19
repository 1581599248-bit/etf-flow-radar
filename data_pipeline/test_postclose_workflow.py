import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PostCloseWorkflowTests(unittest.TestCase):
    def test_postclose_publisher_runs_after_capture_and_retries_later(self):
        text = (ROOT / ".github" / "workflows" / "postclose-etf-publish.yml").read_text("utf-8")
        self.assertIn('"Capture ETF order flow"', text)
        self.assertIn("workflow_run", text)
        for cron in (
            '"30 9 * * 1-5"',
            '"30 10 * * 1-5"',
            '"0 12 * * 1-5"',
        ):
            self.assertIn(cron, text)
        self.assertIn("Resolve latest captured trade date", text)
        self.assertIn("site/data/order_flow/latest.json", text)
        self.assertIn("Attempt full report as soon as end-of-day data is ready", text)
        # 超时分钟数可能随运维需要调整（12m -> 20m 等），只校验命令结构，
        # 避免每次调整 timeout 都卡死发布流程。
        self.assertRegex(
            text,
            r'timeout \d+m python data_pipeline/update_daily_v2\.py --date "\$\{\{ steps\.trade\.outputs\.trade_date \}\}"',
        )
        self.assertIn("continue-on-error: true", text)
        self.assertIn("cancel-in-progress: true", text)
        self.assertIn("Commit verified full report immediately", text)

    def test_capture_workflow_still_persists_order_flow_independently(self):
        text = (ROOT / ".github" / "workflows" / "capture-etf-order-flow.yml").read_text("utf-8")
        self.assertIn("Capture same-day secondary-market ETF order flow", text)
        self.assertIn("git add site/data/order_flow", text)
        self.assertIn("data: capture same-day ETF secondary order flow", text)

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

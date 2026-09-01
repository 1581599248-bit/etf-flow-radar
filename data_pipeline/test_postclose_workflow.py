import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PostCloseWorkflowTests(unittest.TestCase):
    def test_single_automatic_workflow_probes_before_full_build(self):
        text = (ROOT / ".github" / "workflows" / "daily-etf-data.yml").read_text("utf-8")
        for cron in (
            '"37 14 * * 1-5"',
            '"17 18 * * 1-5"',
            '"23 0,4 * * 2-6"',
        ):
            self.assertIn(cron, text)
        self.assertIn("probe_official_shares.py", text)
        self.assertIn("primary", text)
        self.assertIn("secondary", text)
        self.assertIn("actions/upload-artifact@v4", text)
        self.assertIn("independent official-share probes disagree", text)
        self.assertIn("Build schema-v6 production snapshot once", text)
        self.assertIn("timeout 45m python data_pipeline/update_daily_v2.py", text)
        self.assertIn("resolve_publication_target", text)
        self.assertNotIn("order_flow/latest.json", text)
        self.assertNotIn("for attempt in 1 2 3 4", text)
        self.assertIn('attempts=36', text)
        self.assertIn('sleep 300', text)
        self.assertIn("cancel-in-progress: false", text)

    def test_old_postclose_workflow_is_manual_dispatch_only(self):
        text = (ROOT / ".github" / "workflows" / "postclose-etf-publish.yml").read_text("utf-8")
        self.assertIn("workflow_dispatch", text)
        self.assertNotIn("workflow_run", text)
        self.assertNotIn("schedule:", text)
        self.assertIn("gh workflow run daily-etf-data.yml", text)
        self.assertIn("cancel-in-progress: false", text)

    def test_capture_workflow_still_persists_order_flow_independently(self):
        text = (ROOT / ".github" / "workflows" / "capture-etf-order-flow.yml").read_text("utf-8")
        self.assertIn("Capture same-day secondary-market ETF order flow", text)
        self.assertIn('attempts=7', text)
        self.assertIn("git add site/data/order_flow", text)
        self.assertIn("data: capture same-day ETF secondary order flow", text)

    def test_public_render_is_checked_after_a_successful_publish_workflow(self):
        text = (ROOT / ".github" / "workflows" / "verify-render-deploy.yml").read_text("utf-8")
        self.assertIn("workflow_run", text)
        self.assertIn("Official ETF share gate and publish", text)
        self.assertIn("etf-flow-radar-cn.onrender.com/data/latest.json", text)
        self.assertIn("EXPECTED_TRADE_DATE", text)


if __name__ == "__main__":
    unittest.main()

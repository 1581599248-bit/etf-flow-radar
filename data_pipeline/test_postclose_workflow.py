import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PostCloseWorkflowTests(unittest.TestCase):
    def test_single_automatic_workflow_probes_before_full_build(self):
        text = (ROOT / ".github" / "workflows" / "daily-etf-data.yml").read_text("utf-8")
        for cron in (
            '"35 14 * * 1-5"',
            '"15 16 * * 1-5"',
            '"20 0,4 * * 2-6"',
            '"23 1,5,9 * * 0,6"',
        ):
            self.assertIn(cron, text)
        self.assertIn("probe_official_shares.py", text)
        self.assertIn("official SSE/SZSE", text)
        self.assertIn("actions/upload-artifact@v4", text)
        self.assertIn("independent official-share probes disagree", text)
        self.assertIn("Build and audit schema-v6 production snapshot", text)
        self.assertNotIn("for attempt in 1 2 3", text)
        self.assertIn("python data_pipeline/audit_snapshot_v6.py", text)
        self.assertIn("timeout 25m python data_pipeline/update_daily_v2.py", text)
        self.assertIn("resolve_publication_target", text)
        self.assertNotIn("order_flow/latest.json", text)
        self.assertNotIn("for attempt in 1 2 3 4", text)
        self.assertIn('attempts=8', text)
        self.assertIn('WeChat alert on failure', text)
        self.assertIn('PUSHPLUS_TOKEN', text)
        self.assertIn('sleep 300', text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn('ETF_SKIP_RETURN_PROXIES: "1"', text)

    def test_capture_workflow_still_persists_order_flow_independently(self):
        text = (ROOT / ".github" / "workflows" / "capture-etf-order-flow.yml").read_text("utf-8")
        self.assertIn("Capture same-day secondary-market ETF order flow", text)
        self.assertNotIn('attempts=7', text)
        self.assertIn("git add site/data/order_flow", text)
        self.assertIn("data: capture same-day ETF secondary order flow", text)

    def test_public_render_is_checked_only_after_a_snapshot_commit(self):
        text = (ROOT / ".github" / "workflows" / "verify-render-deploy.yml").read_text("utf-8")
        self.assertIn("site/data/**", text)
        self.assertNotIn("workflow_run", text)
        self.assertIn("etf-flow-radar-cn.onrender.com/data/latest.json", text)
        self.assertIn("EXPECTED_TRADE_DATE", text)


if __name__ == "__main__":
    unittest.main()

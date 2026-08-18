import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PostCloseWorkflowTests(unittest.TestCase):
    def test_postclose_publisher_runs_after_precise_secondary_capture(self):
        text = (ROOT / ".github" / "workflows" / "postclose-etf-publish.yml").read_text("utf-8")
        self.assertIn('"Capture ETF secondary trading facts"', text)
        self.assertIn("workflow_run", text)
        for cron in (
            '"30 9 * * 1-5"',
            '"30 10 * * 1-5"',
            '"0 12 * * 1-5"',
        ):
            self.assertIn(cron, text)
        self.assertIn("Resolve latest captured trade date", text)
        self.assertIn("site/data/order_flow/latest.json", text)
        self.assertIn("Attempt unified full report as soon as end-of-day data is ready", text)
        self.assertIn('timeout 12m python data_pipeline/update_daily_v3.py --date "${{ steps.trade.outputs.trade_date }}"', text)
        self.assertIn("python data_pipeline/audit_snapshot_v7.py", text)
        self.assertIn("python data_pipeline/audit_precision_v7.py", text)
        self.assertIn("continue-on-error: true", text)
        self.assertIn("cancel-in-progress: true", text)
        self.assertIn("Commit verified full report immediately", text)
        self.assertNotIn("update_daily_v2.py --date", text)
        self.assertNotIn("audit_snapshot_v6.py", text)

    def test_postclose_failure_bootstraps_only_the_last_verified_snapshot(self):
        text = (ROOT / ".github" / "workflows" / "postclose-etf-publish.yml").read_text("utf-8")
        self.assertIn("Restore repository data before bootstrap fallback", text)
        self.assertIn("git restore --source=HEAD --staged --worktree -- site/data", text)
        self.assertIn("Bootstrap the last verified snapshot to Data Contract 7.0", text)
        self.assertIn("python data_pipeline/migrate_verified_snapshot_v7.py", text)
        self.assertIn("Audit bootstrapped verified snapshot", text)
        self.assertIn("Audit bootstrapped monetary precision", text)
        self.assertIn("python data_pipeline/audit_precision_v7.py", text)
        self.assertIn("Validate bootstrapped client bundle", text)
        self.assertIn("data: migrate last verified snapshot to Contract 7.0", text)
        self.assertIn("without changing its trade date or canonical one-day market facts", text)
        # A failed/partial fresh build is never allowed to become the migration input.
        self.assertLess(
            text.index("git restore --source=HEAD --staged --worktree -- site/data"),
            text.index("python data_pipeline/migrate_verified_snapshot_v7.py"),
        )
        # The migrated fallback must pass both semantic and precision audits before commit.
        migrate_at = text.index("python data_pipeline/migrate_verified_snapshot_v7.py")
        semantic_at = text.rindex("python data_pipeline/audit_snapshot_v7.py")
        precision_at = text.rindex("python data_pipeline/audit_precision_v7.py")
        commit_at = text.index("Commit bootstrap migration if needed")
        self.assertLess(migrate_at, semantic_at)
        self.assertLess(semantic_at, precision_at)
        self.assertLess(precision_at, commit_at)

    def test_capture_workflow_persists_secondary_trading_facts_independently(self):
        text = (ROOT / ".github" / "workflows" / "capture-etf-order-flow.yml").read_text("utf-8")
        self.assertIn("Capture same-day ETF secondary-market aggressor statistics", text)
        self.assertIn("python data_pipeline/capture_order_flow_v3.py", text)
        self.assertIn("git add site/data/order_flow", text)
        self.assertIn("data: capture same-day ETF secondary trading facts", text)
        self.assertNotIn("secondary-market ETF order flow", text)

    def test_morning_fallback_uses_the_same_unified_pipeline(self):
        text = (ROOT / ".github" / "workflows" / "daily-etf-data.yml").read_text("utf-8")
        for cron in (
            '"0 21 * * 1-5"',
            '"0 22 * * 1-5"',
            '"0 23 * * 1-5"',
            '"0 0 * * 2-6"',
        ):
            self.assertIn(cron, text)
        self.assertIn("python data_pipeline/update_daily_v3.py", text)
        self.assertIn("python data_pipeline/audit_snapshot_v7.py", text)
        self.assertIn("python data_pipeline/audit_precision_v7.py", text)
        self.assertNotIn("python data_pipeline/update_daily_v2.py", text)


if __name__ == "__main__":
    unittest.main()

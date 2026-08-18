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
        self.assertIn("continue-on-error: true", text)
        self.assertIn("cancel-in-progress: true", text)
        self.assertIn("Commit verified full report immediately", text)
        self.assertNotIn("update_daily_v2.py --date", text)
        self.assertNotIn("audit_snapshot_v6.py", text)

    def test_contract_bootstrap_is_published_before_any_fresh_data_dependency(self):
        text = (ROOT / ".github" / "workflows" / "postclose-etf-publish.yml").read_text("utf-8")
        self.assertIn("Bootstrap last verified snapshot to Data Contract 7.0", text)
        self.assertIn("python data_pipeline/migrate_verified_snapshot_v7.py", text)
        self.assertIn("Audit bootstrapped verified snapshot", text)
        self.assertIn("Validate bootstrapped client bundle", text)
        self.assertIn("Publish bootstrapped snapshot before broader regression tests", text)
        self.assertIn("data: migrate last verified snapshot to Contract 7.0", text)
        self.assertIn("Run deterministic pipeline tests before fresh rebuild", text)
        self.assertIn("without changing its trade date or canonical one-day market facts", text)

        migration = text.index("python data_pipeline/migrate_verified_snapshot_v7.py")
        audit = text.index("python data_pipeline/audit_snapshot_v7.py", migration)
        publish = text.index("Publish bootstrapped snapshot before broader regression tests")
        broad_tests = text.index("Run deterministic pipeline tests before fresh rebuild")
        resolve = text.index("Resolve latest captured trade date")
        fresh_build = text.index("Attempt unified full report as soon as end-of-day data is ready")

        # Availability recovery must be independent of today's secondary file,
        # broad regression suite and fresh upstream collection.
        self.assertLess(migration, audit)
        self.assertLess(audit, publish)
        self.assertLess(publish, broad_tests)
        self.assertLess(broad_tests, resolve)
        self.assertLess(resolve, fresh_build)

    def test_fresh_build_failure_cannot_replace_the_bootstrapped_repository_snapshot(self):
        text = (ROOT / ".github" / "workflows" / "postclose-etf-publish.yml").read_text("utf-8")
        self.assertIn("continue-on-error: true", text)
        self.assertIn("Explain deferred fresh-data publication", text)
        self.assertIn("The last already-verified snapshot remains available under Data Contract 7.0", text)
        self.assertLess(
            text.index("Publish bootstrapped snapshot before broader regression tests"),
            text.index("Attempt unified full report as soon as end-of-day data is ready"),
        )
        self.assertLess(
            text.index("Attempt unified full report as soon as end-of-day data is ready"),
            text.index("Commit verified full report immediately"),
        )

    def test_capture_workflow_persists_secondary_trading_facts_independently(self):
        text = (ROOT / ".github" / "workflows" / "capture-etf-order-flow.yml").read_text("utf-8")
        self.assertIn("Capture same-day ETF secondary-market aggressor statistics", text)
        self.assertIn("python data_pipeline/capture_order_flow_v3.py", text)
        self.assertIn("git add site/data/order_flow", text)
        self.assertIn("data: capture same-day ETF secondary trading facts", text)
        self.assertNotIn("secondary-market ETF order flow", text)

    def test_morning_fallback_uses_the_same_bootstrap_first_contract(self):
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
        self.assertIn("python data_pipeline/migrate_verified_snapshot_v7.py", text)
        self.assertIn("Publish bootstrapped snapshot before fresh rebuild", text)
        self.assertIn("Run deterministic pipeline tests before fresh rebuild", text)
        self.assertIn("continue-on-error: true", text)
        self.assertIn("Explain deferred fresh-data publication", text)
        self.assertNotIn("python data_pipeline/update_daily_v2.py", text)

        bootstrap = text.index("python data_pipeline/migrate_verified_snapshot_v7.py")
        audit = text.index("python data_pipeline/audit_snapshot_v7.py", bootstrap)
        publish = text.index("Publish bootstrapped snapshot before fresh rebuild")
        tests = text.index("Run deterministic pipeline tests before fresh rebuild")
        fresh = text.index("Build unified production snapshot")
        self.assertLess(bootstrap, audit)
        self.assertLess(audit, publish)
        self.assertLess(publish, tests)
        self.assertLess(tests, fresh)


if __name__ == "__main__":
    unittest.main()

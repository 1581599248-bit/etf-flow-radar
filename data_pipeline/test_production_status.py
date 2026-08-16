import unittest

import update_daily_production as production


class ProductionStatusTests(unittest.TestCase):
    def test_handled_guard_note_keeps_verified_status(self):
        snapshot = {"status": "verified", "quality": {"issues": []}}
        production.production_append_issue(
            snapshot,
            "warning",
            "price_nav_guard",
            "handled by NAV fallback",
        )
        self.assertEqual(snapshot["status"], "verified")
        self.assertEqual(snapshot["quality"]["issues"][0]["severity"], "info")

    def test_critical_guard_still_fails_closed(self):
        snapshot = {"status": "verified", "quality": {"issues": []}}
        production.production_append_issue(
            snapshot,
            "critical",
            "single_etf_extreme_flow",
            "unresolved anomaly",
        )
        self.assertEqual(snapshot["status"], "failed")


if __name__ == "__main__":
    unittest.main()

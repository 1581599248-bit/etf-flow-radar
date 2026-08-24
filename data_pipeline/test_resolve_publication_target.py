import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from resolve_publication_target import resolve_target


class ResolvePublicationTargetTests(unittest.TestCase):
    def test_uses_current_beijing_trade_date_after_close_without_order_flow_input(self):
        now = datetime(2026, 8, 24, 22, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(resolve_target(now=now).isoformat(), "2026-08-24")

    def test_overnight_retry_keeps_prior_completed_trade_date(self):
        now = datetime(2026, 8, 25, 2, 10, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(resolve_target(now=now).isoformat(), "2026-08-24")

    def test_weekend_retries_the_prior_weekday(self):
        now = datetime(2026, 8, 23, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(resolve_target(now=now).isoformat(), "2026-08-21")

    def test_operator_override_is_exact_and_validated(self):
        self.assertEqual(resolve_target("2026-08-21").isoformat(), "2026-08-21")
        with self.assertRaises(ValueError):
            resolve_target("2026-08-32")


if __name__ == "__main__":
    unittest.main()

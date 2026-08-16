import unittest
from unittest.mock import patch

import pandas as pd

import update_daily_production as production


class ProductionReconciliationTests(unittest.TestCase):
    def test_secondary_vendor_never_overwrites_official_share_jump(self):
        codes = [f"{i:06d}" for i in range(120)]
        previous = pd.DataFrame({
            "code": codes,
            "name": [f"ETF{i}" for i in range(120)],
            "shares": [float((i + 10) * 100_000_000) for i in range(120)],
        })
        current = previous.copy()
        current.loc[current["code"] == "000050", "shares"] *= 3
        secondary = pd.DataFrame({
            "code": codes,
            "secondary_shares_raw": [float(i + 10) for i in range(120)],
        })
        kept, audit = production.production_audit_current_shares(current, previous, secondary)
        official = float(current.loc[current["code"] == "000050", "shares"].iloc[0])
        actual = float(kept.loc[kept["code"] == "000050", "shares"].iloc[0])
        self.assertEqual(actual, official)
        self.assertEqual(audit["repaired"], [])
        self.assertEqual(len(audit["disagreements"]), 1)
        self.assertEqual(audit["disagreements"][0]["action"], "official_retained_event_check_required")

    def test_588710_split_is_confirmed_by_inverse_nav_move(self):
        panel = pd.DataFrame({
            "code": ["588710"],
            "fund_name": ["科创半导体设备ETF华泰柏瑞"],
            "nav": [1.0532],
            "prev_nav": [3.1344],
            "fund_type": ["股票型"],
            "query_date": [pd.Timestamp("2026-08-14").date()],
        })
        with patch.object(production, "_get_ths_day", return_value=panel):
            self.assertTrue(
                production.production_confirm_split(
                    "588710", pd.Timestamp("2026-08-13").date(), pd.Timestamp("2026-08-14").date(), 3.0
                )
            )

    def test_large_subscription_with_stable_nav_is_not_split(self):
        panel = pd.DataFrame({
            "code": ["000050"],
            "fund_name": ["测试ETF"],
            "nav": [1.01],
            "prev_nav": [1.00],
            "fund_type": ["股票型"],
            "query_date": [pd.Timestamp("2026-08-14").date()],
        })
        with patch.object(production, "_get_ths_day", return_value=panel):
            self.assertFalse(
                production.production_confirm_split(
                    "000050", pd.Timestamp("2026-08-13").date(), pd.Timestamp("2026-08-14").date(), 2.0
                )
            )

    def test_hot_theme_is_rolled_back_to_sw_level_one_parent(self):
        snapshot = {
            "etfs": [
                {"code": "588170", "name": "科创半导体ETF华夏", "kind": "industry", "groupId": "elec_semiconductor", "aum": 400.0, "flow1d": -3.0, "flow5d": 2.0, "flow20d": 10.0},
                {"code": "512480", "name": "半导体ETF国联安", "kind": "industry", "groupId": "elec_semiconductor", "aum": 200.0, "flow1d": -1.0, "flow5d": 1.0, "flow20d": 3.0},
                {"code": "561600", "name": "消费电子ETF", "kind": "industry", "groupId": "elec_consumer", "aum": 20.0, "flow1d": 0.5, "flow5d": 0.2, "flow20d": 0.4},
            ]
        }
        rollups = production._build_industry_rollups(snapshot)
        electronics = next(r for r in rollups if r["id"] == "sw_electronics")
        self.assertEqual(electronics["etfCount"], 3)
        self.assertAlmostEqual(electronics["flow1d"], -3.5)
        self.assertIn("elec_semiconductor", electronics["leafGroups"])

    def test_a_share_scope_excludes_cross_border_even_when_stock_type(self):
        self.assertTrue(production._is_a_share_equity("半导体ETF", "半导体ETF", "股票型"))
        self.assertFalse(production._is_a_share_equity("恒生科技ETF", "恒生科技ETF", "股票型"))
        self.assertFalse(production._is_a_share_equity("国债ETF", "国债ETF", "债券型"))


if __name__ == "__main__":
    unittest.main()

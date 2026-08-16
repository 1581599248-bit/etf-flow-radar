import unittest

import pandas as pd

import update_daily_guarded as guarded


class GuardrailTests(unittest.TestCase):
    def test_infer_secondary_share_scale(self):
        official = pd.DataFrame({
            "code": [f"{i:06d}" for i in range(120)],
            "shares": [float((i + 1) * 100_000_000) for i in range(120)],
        })
        secondary = pd.DataFrame({
            "code": official["code"],
            "secondary_shares_raw": [float(i + 1) for i in range(120)],
        })
        scale, error, common = guarded.infer_secondary_share_scale(official, secondary)
        self.assertEqual(scale, 100_000_000.0)
        self.assertEqual(common, 120)
        self.assertAlmostEqual(error or 0.0, 0.0, places=8)

    def test_repair_extreme_official_row_when_secondary_is_continuous(self):
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
        repaired, audit = guarded.repair_current_shares(current, previous, secondary)
        expected = float(previous.loc[previous["code"] == "000050", "shares"].iloc[0])
        actual = float(repaired.loc[repaired["code"] == "000050", "shares"].iloc[0])
        self.assertEqual(actual, expected)
        self.assertEqual(audit["status"], "usable")
        self.assertEqual(len(audit["repaired"]), 1)

    def test_do_not_repair_independently_confirmed_large_subscription(self):
        codes = [f"{i:06d}" for i in range(120)]
        previous = pd.DataFrame({
            "code": codes,
            "name": [f"ETF{i}" for i in range(120)],
            "shares": [float((i + 10) * 100_000_000) for i in range(120)],
        })
        current = previous.copy()
        current.loc[current["code"] == "000050", "shares"] *= 2
        secondary = pd.DataFrame({
            "code": codes,
            "secondary_shares_raw": [
                float(current.loc[current["code"] == code, "shares"].iloc[0] / 100_000_000)
                for code in codes
            ],
        })
        repaired, audit = guarded.repair_current_shares(current, previous, secondary)
        expected = float(current.loc[current["code"] == "000050", "shares"].iloc[0])
        actual = float(repaired.loc[repaired["code"] == "000050", "shares"].iloc[0])
        self.assertEqual(actual, expected)
        self.assertEqual(len(audit["repaired"]), 0)

    def test_candidate_split_factor(self):
        self.assertEqual(guarded._candidate_split_factor(100.0, 300.0), 3.0)
        self.assertEqual(guarded._candidate_split_factor(300.0, 100.0), 1 / 3)
        self.assertIsNone(guarded._candidate_split_factor(100.0, 135.0))

    def test_extreme_flow_fails_closed(self):
        snapshot = {
            "status": "verified",
            "quality": {"issues": []},
            "etfs": [{"code": "588710", "name": "测试ETF", "aum": 335.0, "flow1d": 222.0}],
        }
        guarded._apply_flow_sanity_gate(snapshot)
        self.assertEqual(snapshot["status"], "failed")
        checks = {item["check"] for item in snapshot["quality"]["issues"]}
        self.assertIn("single_etf_extreme_flow", checks)


if __name__ == "__main__":
    unittest.main()

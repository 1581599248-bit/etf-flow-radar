import unittest
from datetime import date
from unittest.mock import patch

import pandas as pd

import historical_nav_fallback as fallback


class HistoricalNavFallbackTests(unittest.TestCase):
    def test_existing_guarded_source_remains_first_choice(self):
        day = date(2026, 8, 18)
        expected = pd.DataFrame({
            "code": ["510300"],
            "price_name": ["沪深300ETF"],
            "reference_price": [4.2],
            "reference_price_type": ["NAV"],
        })
        with patch.object(fallback.guarded, "guarded_fetch_reference_prices", return_value=expected), patch.object(
            fallback.base.ak, "fund_etf_category_ths"
        ) as ths:
            result = fallback.fetch_reference_prices(day)
        ths.assert_not_called()
        self.assertEqual(result.to_dict("records"), expected.to_dict("records"))

    def test_historical_exact_date_nav_recovers_after_realtime_window(self):
        day = date(2026, 8, 18)
        ths_frame = pd.DataFrame({
            "基金代码": ["510300", "588000"],
            "基金名称": ["沪深300ETF", "科创50ETF"],
            "当前-单位净值": [4.2, 1.1],
            "查询日期": ["2026-08-18", "2026-08-18"],
        })
        with patch.object(
            fallback.guarded,
            "guarded_fetch_reference_prices",
            side_effect=ValueError("no ETF NAV column for 2026-08-18"),
        ), patch.object(fallback.base.ak, "fund_etf_category_ths", return_value=ths_frame):
            result = fallback.fetch_reference_prices(day)
        self.assertEqual(set(result["code"]), {"510300", "588000"})
        self.assertEqual(set(result["reference_price_type"]), {"NAV_THS_EXACT_DATE"})
        self.assertAlmostEqual(float(result.loc[result["code"] == "510300", "reference_price"].iloc[0]), 4.2)

    def test_fallback_rejects_wrong_query_date(self):
        day = date(2026, 8, 18)
        wrong = pd.DataFrame({
            "基金代码": ["510300"],
            "基金名称": ["沪深300ETF"],
            "当前-单位净值": [4.2],
            "查询日期": ["2026-08-19"],
        })
        with patch.object(
            fallback.guarded, "guarded_fetch_reference_prices", side_effect=ValueError("realtime unavailable")
        ), patch.object(fallback.base.ak, "fund_etf_category_ths", return_value=wrong):
            with self.assertRaises(RuntimeError):
                fallback.fetch_reference_prices(day)


if __name__ == "__main__":
    unittest.main()

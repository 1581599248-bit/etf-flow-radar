import unittest
from datetime import date, timedelta

import pandas as pd

import flow_model_v2 as model


class FlowModelV2Tests(unittest.TestCase):
    def _snapshot(self):
        return {
            "universe": [
                {"code": "510300", "name": "沪深300ETF华泰柏瑞", "shares": 1_100_000_000,
                 "referencePrice": 4.90, "referencePriceType": "AVG"},
                {"code": "513100", "name": "纳指ETF", "shares": 900_000_000,
                 "referencePrice": 2.10, "referencePriceType": "AVG"},
            ],
            "etfs": [
                {"code": "510300", "name": "沪深300ETF华泰柏瑞", "groupId": "hs300",
                 "groupName": "沪深300", "kind": "broad", "referencePrice": 4.90,
                 "referencePriceType": "AVG", "flow1d": 0.0, "flow5d": 0.0, "flow20d": 0.0,
                 "aum": 0.0},
            ],
            "groups": [
                {"id": "hs300", "name": "沪深300", "kind": "broad", "flow1d": 0.0,
                 "flow5d": 0.0, "flow20d": 0.0, "aum": 0.0,
                 "relativeReturn20d": 0.5, "return1d": 0.0, "return5d": 0.0,
                 "return20d": 0.0, "representative": {"code": "510300", "name": "沪深300ETF华泰柏瑞"}},
            ],
            "quality": {},
        }

    def _ths(self):
        return pd.DataFrame([
            {"code": "510300", "fund_name": "华泰柏瑞沪深300ETF", "nav": 5.0,
             "prev_nav": 4.8, "fund_type": "股票型"},
            {"code": "513100", "fund_name": "国泰纳斯达克100ETF", "nav": 2.0,
             "prev_nav": 2.0, "fund_type": "股票型"},
        ])

    def _window(self):
        end = date(2026, 8, 14)
        rows = []
        for i in range(21):
            d = end - timedelta(days=20 - i)
            # Calendar spacing is irrelevant to the pure flow model: positions in
            # the list represent trading sessions supplied by the transport layer.
            rows.append((d, pd.DataFrame([
                {"code": "510300", "shares": 1_000_000_000 + i * 5_000_000},
                {"code": "513100", "shares": 950_000_000 - i * 2_500_000},
            ])))
        rows[-1] = (end, pd.DataFrame([
            {"code": "510300", "shares": 1_100_000_000},
            {"code": "513100", "shares": 900_000_000},
        ]))
        return rows

    def test_primary_flow_uses_nav_and_keeps_average_price_as_comparison(self):
        snapshot = self._snapshot()
        model.apply_flow_model(snapshot, date(2026, 8, 14), self._window(), self._ths(), None)
        etf = snapshot["etfs"][0]
        self.assertEqual(etf["flow1d"], 0.25)
        self.assertEqual(etf["primaryFlow1d"], 0.25)
        self.assertEqual(etf["flow1dAvgPriceEstimate"], 0.24)
        self.assertEqual(etf["flowValuation"], "sameDayUnitNAV")
        self.assertEqual(snapshot["market"]["metric"], "primaryMarketNetSubscriptionEstimate")

    def test_market_scopes_are_explicit_and_crossborder_does_not_enter_ashare_total(self):
        snapshot = self._snapshot()
        model.apply_flow_model(snapshot, date(2026, 8, 14), self._window(), self._ths(), None)
        scopes = snapshot["flowMetrics"]["primaryMarket"]["scopeTotals"]
        self.assertEqual(scopes["aShareStockEtf"]["etfCount"], 1)
        self.assertEqual(scopes["stockEtfIncludingCrossBorder"]["etfCount"], 2)
        self.assertEqual(snapshot["market"]["etfCount"], 1)

    def test_asset_scope_keeps_money_separate_from_bond(self):
        self.assertEqual(model._asset_scope("货币ETF", "华宝现金添益ETF", "货币型"), "moneyEtf")
        self.assertEqual(model._asset_scope("添富快线ETF", "汇添富收益快线货币ETF", "其他"), "moneyEtf")
        self.assertEqual(model._asset_scope("国债ETF", "国泰上证5年期国债ETF", "债券型"), "bondEtf")
        self.assertEqual(model._asset_scope("黄金ETF", "华安黄金易ETF", "其他"), "commodityEtf")
        self.assertEqual(model._asset_scope("纳指ETF", "国泰纳斯达克100ETF", "股票型"), "crossBorderStockEtf")
        self.assertEqual(model._asset_scope("沪深300ETF", "华泰柏瑞沪深300ETF", "股票型"), "aShareStockEtf")

    def test_secondary_order_flow_never_overwrites_primary_and_requires_exact_date(self):
        snapshot = self._snapshot()
        spot = pd.DataFrame({
            "代码": ["510300"],
            "主力净流入-净额": [-900_000_000],
            "数据日期": ["2026-08-14"],
        })
        model.apply_flow_model(snapshot, date(2026, 8, 14), self._window(), self._ths(), spot)
        etf = snapshot["etfs"][0]
        self.assertEqual(etf["flow1d"], 0.25)
        self.assertEqual(etf["secondaryMainOrderFlow1d"], -9.0)
        self.assertEqual(snapshot["flowMetrics"]["secondaryMarketOrderFlow"]["status"], "available")

        stale = self._snapshot()
        stale_spot = spot.assign(数据日期="2026-08-17")
        model.apply_flow_model(stale, date(2026, 8, 14), self._window(), self._ths(), stale_spot)
        self.assertIsNone(stale["etfs"][0]["secondaryMainOrderFlow1d"])
        self.assertEqual(stale["flowMetrics"]["secondaryMarketOrderFlow"]["status"], "unavailable")

    def test_split_adjusted_window_generates_real_redemption_not_fake_subscription(self):
        snapshot = {
            "universe": [{"code": "588710", "name": "科创半导体设备ETF华泰柏瑞",
                           "shares": 10_656_885_000, "referencePrice": 1.0532,
                           "referencePriceType": "NAV"}],
            "etfs": [{"code": "588710", "name": "科创半导体设备ETF华泰柏瑞",
                      "groupId": "elec_semiconductor", "groupName": "半导体", "kind": "industry",
                      "flow1d": 0.0, "flow5d": 0.0, "flow20d": 0.0, "aum": 0.0}],
            "groups": [], "quality": {},
        }
        prev_comparable = 10_776_885_000
        window = [
            (date(2026, 8, 13), pd.DataFrame([{"code": "588710", "shares": prev_comparable}])),
            (date(2026, 8, 14), pd.DataFrame([{"code": "588710", "shares": 10_656_885_000}])),
        ]
        ths = pd.DataFrame([{"code": "588710", "fund_name": "华泰柏瑞科创半导体设备ETF",
                             "nav": 1.0532, "prev_nav": 3.1344, "fund_type": "股票型"}])
        model.apply_flow_model(snapshot, date(2026, 8, 14), window, ths, None)
        self.assertAlmostEqual(snapshot["etfs"][0]["flow1d"], -1.26, places=2)


if __name__ == "__main__":
    unittest.main()

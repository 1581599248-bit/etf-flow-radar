import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import update_daily_v2 as v2


class UpdateDailyV2Tests(unittest.TestCase):
    def test_persisted_order_flow_is_preferred_over_current_spot(self):
        day = date(2026, 8, 14)
        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp)
            folder = public / "order_flow"
            folder.mkdir(parents=True)
            payload = {
                "schemaVersion": 1,
                "tradeDate": day.isoformat(),
                "metric": "secondaryMarketMainOrderFlow",
                "etfs": [{"code": "510300", "name": "沪深300ETF华泰柏瑞", "mainOrderFlow1d": -2.5, "amount": 10.0}],
            }
            (folder / f"{day.isoformat()}.json").write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
            with patch.object(v2.base, "PUBLIC", public), patch.object(v2.guarded, "_get_spot") as live:
                frame = v2._load_secondary_spot(day)
            live.assert_not_called()
            self.assertEqual(frame.loc[0, "代码"], "510300")
            self.assertEqual(frame.loc[0, "数据日期"], day.isoformat())
            self.assertEqual(float(frame.loc[0, "主力净流入-净额"]), -250_000_000.0)

    def test_persisted_v2_trading_flow_restores_trade_net_fields(self):
        day = date(2026, 8, 14)
        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp)
            folder = public / "order_flow"
            folder.mkdir(parents=True)
            payload = {
                "schemaVersion": 2,
                "tradeDate": day.isoformat(),
                "metric": "secondaryMarketETFTradingFlow",
                "etfs": [{
                    "code": "510300", "name": "沪深300ETF华泰柏瑞",
                    "tradeInflow1d": 12.0, "tradeOutflow1d": 8.0,
                    "tradeNetFlow1d": 4.0, "mainOrderFlow1d": 1.5, "amount": 20.0,
                }],
            }
            (folder / f"{day.isoformat()}.json").write_text(json.dumps(payload, ensure_ascii=False), "utf-8")
            with patch.object(v2.base, "PUBLIC", public), patch.object(v2.guarded, "_get_spot") as live:
                frame = v2._load_secondary_spot(day)
            live.assert_not_called()
            self.assertEqual(float(frame.loc[0, "当日交易净额"]), 400_000_000.0)
            self.assertEqual(float(frame.loc[0, "当日交易流入"]), 1_200_000_000.0)
            self.assertEqual(float(frame.loc[0, "当日交易流出"]), 800_000_000.0)

    def test_missing_fact_file_falls_back_to_live_spot_with_downstream_date_guard(self):
        day = date(2026, 8, 14)
        live_frame = pd.DataFrame({"代码": ["510300"], "数据日期": ["2026-08-17"]})
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(v2.base, "PUBLIC", Path(tmp)), patch.object(v2.guarded, "_get_spot", return_value=live_frame) as live:
                frame = v2._load_secondary_spot(day)
        live.assert_called_once()
        self.assertEqual(frame.iloc[0]["数据日期"], "2026-08-17")

    def test_trade_net_scope_uses_only_a_share_stock_etfs_for_headline(self):
        day = date(2026, 8, 14)
        snapshot = {
            "universe": [
                {"code": "510300", "name": "沪深300ETF华泰柏瑞"},
                {"code": "513100", "name": "纳指ETF"},
            ],
            "etfs": [{"code": "510300", "name": "沪深300ETF华泰柏瑞"}],
            "flowMetrics": {},
        }
        ths = pd.DataFrame([
            {"code": "510300", "fund_name": "华泰柏瑞沪深300ETF", "fund_type": "股票型"},
            {"code": "513100", "fund_name": "国泰纳斯达克100ETF", "fund_type": "股票型"},
        ])
        spot = pd.DataFrame({
            "代码": ["510300", "513100"],
            "当日交易净额": [200_000_000, -100_000_000],
            "当日交易流入": [600_000_000, 200_000_000],
            "当日交易流出": [400_000_000, 300_000_000],
            "数据日期": [day.isoformat(), day.isoformat()],
        })
        v2._add_trade_net_flow(snapshot, day, ths, spot)
        totals = snapshot["flowMetrics"]["secondaryMarketTradeFlow"]["scopeTotals"]
        self.assertEqual(totals["aShareStockEtf"]["netFlow1d"], 2.0)
        self.assertEqual(totals["stockEtfIncludingCrossBorder"]["netFlow1d"], 1.0)
        self.assertEqual(snapshot["etfs"][0]["secondaryTradeNetFlow1d"], 2.0)

    @staticmethod
    def _groups():
        return [
            {"id": "hs300", "name": "沪深300", "kind": "broad", "flow1d": -3.0},
            {"id": "csi500", "name": "中证500", "kind": "broad", "flow1d": 1.0},
            {"id": "sw_media", "name": "传媒", "kind": "industry", "flow1d": 1.8},
            {"id": "elec_chip", "name": "芯片", "kind": "industry", "parent": "sw_electronics", "flow1d": -14.4},
            {"id": "elec_semiconductor", "name": "半导体", "kind": "industry", "parent": "sw_electronics", "flow1d": -23.4},
        ]

    @staticmethod
    def _legacy_conclusion(obj):
        obj["conclusion"] = {
            "headline": "旧口径。宽基中1个流出、1个流入；申万一级行业资金流入居前的是传媒，流出最多的是电子。",
            "facts": ["宽基事实", "申万一级行业旧事实", "单ETF事实"],
        }

    def test_strength_thresholds_use_relative_market_scale(self):
        self.assertEqual(v2._trade_strength(19.9, 2000.0), "balanced")
        self.assertEqual(v2._trade_strength(20.0, 2000.0), "small")
        self.assertEqual(v2._trade_strength(60.0, 2000.0), "clear")
        self.assertEqual(v2._trade_strength(120.0, 2000.0), "large")
        self.assertEqual(v2._primary_strength(9.9, 20000.0), "flat")
        self.assertEqual(v2._primary_strength(10.0, 20000.0), "small")
        self.assertEqual(v2._primary_strength(40.0, 20000.0), "clear")
        self.assertEqual(v2._primary_strength(100.0, 20000.0), "large")

    def test_market_flow_headline_covers_direction_strength_and_concise_watch_copy(self):
        prohibited = ("资金离场", "持续撤离", "股灾", "连续赎回", "资金出逃", "下一交易日重点看")
        for trade in (None, -150.0, -80.0, 0.0, 80.0, 150.0):
            for primary in (-120.0, -60.0, 0.0, 60.0, 120.0):
                headline = v2._market_flow_headline(trade, primary, 2000.0 if trade is not None else None, 20000.0, -100.0, -200.0)
                self.assertIn("\n—— ", headline)
                self.assertFalse(any(text in headline for text in prohibited))
                self.assertTrue(headline.endswith("。"))

    def test_market_flow_headline_uses_relative_history_only_when_meaningful(self):
        stronger = v2._market_flow_headline(80.0, 100.0, 2000.0, 20000.0, 100.0, 200.0)
        opposite = v2._market_flow_headline(-80.0, -100.0, 2000.0, 20000.0, 100.0, 200.0)
        quiet = v2._market_flow_headline(80.0, 100.0, 2000.0, 20000.0, 0.0, 0.0)
        self.assertIn("当日幅度高于近5日均值", stronger)
        self.assertIn("与近阶段平均方向相反", opposite)
        self.assertNotIn("近5日", quiet)

    def test_homepage_headline_uses_strength_copy_and_visible_sector_layer(self):
        snapshot = {
            "market": {
                "flow1d": -48.3,
                "aum": 5000.0,
                "increaseEtfCount1d": 231,
                "decreaseEtfCount1d": 409,
                "unchangedEtfCount1d": 607,
                "flow5dEndpoint": 20.0,
            },
            "groups": self._groups(),
            "flowMetrics": {
                "secondaryMarketTradeFlow": {
                    "scopeTotals": {
                        "aShareStockEtf": {
                            "netFlow1d": 198.4,
                            "inflow1d": 2599.2,
                            "outflow1d": 2400.8,
                        }
                    }
                }
            },
        }
        with patch.object(v2.production, "_regenerate_conclusion", side_effect=self._legacy_conclusion):
            v2._regenerate_v2_conclusion(snapshot)
        headline = snapshot["conclusion"]["headline"]
        self.assertIn("盘中承接偏强但份额净流出", headline)
        self.assertIn("关注明日两端是否收敛", headline)
        self.assertNotIn("宽基", headline)
        self.assertNotIn("申万一级和主题行业", headline)
        self.assertNotIn("A股股票ETF当日合计", headline)
        self.assertNotIn("流出最多的是电子", headline)
        self.assertIn("净流出居前为半导体-23.4亿", snapshot["conclusion"]["facts"][2])
        self.assertIn("净流入居前为传媒+1.8亿", snapshot["conclusion"]["facts"][2])
        self.assertIn("共2组，1个净流出、1个净流入", snapshot["conclusion"]["facts"][0])
        self.assertIn("净流出居前为沪深300-3.0亿", snapshot["conclusion"]["facts"][0])

    def test_homepage_summary_has_four_fixed_data_modules(self):
        snapshot = {
            "market": {"flow1d": 1.0, "aum": 20000.0},
            "groups": self._groups() + [{"id": "value", "name": "价值", "kind": "style", "flow1d": 2.0}],
            "etfs": [
                {"name": "ETF甲", "flow1d": 3.0},
                {"name": "ETF乙", "flow1d": -4.0},
            ],
            "flowMetrics": {"secondaryMarketTradeFlow": {"scopeTotals": {"aShareStockEtf": {
                "netFlow1d": 0.0, "inflow1d": 100.0, "outflow1d": 100.0,
            }}}},
        }
        with patch.object(v2.production, "_regenerate_conclusion", side_effect=self._legacy_conclusion):
            v2._regenerate_v2_conclusion(snapshot)
        facts = snapshot["conclusion"]["facts"]
        self.assertEqual(len(facts), 4)
        self.assertIn("共2组，1个净流出、1个净流入", facts[0])
        self.assertIn("净流入居前为价值+2.0亿", facts[1])
        self.assertIn("净流出居前为半导体-23.4亿", facts[2])
        self.assertIn("净流入最大为ETF甲+3.0亿", facts[3])
        self.assertIn("净流出最大为ETF乙-4.0亿", facts[3])

    def test_homepage_headline_keeps_primary_share_flow_when_secondary_is_missing(self):
        snapshot = {
            "market": {
                "flow1d": 12.6,
                "aum": 2000.0,
                "increaseEtfCount1d": 300,
                "decreaseEtfCount1d": 200,
                "unchangedEtfCount1d": 700,
                "flow5dEndpoint": 15.0,
            },
            "groups": self._groups(),
            "flowMetrics": {"secondaryMarketTradeFlow": {"status": "unavailable", "scopeTotals": {}}},
        }
        with patch.object(v2.production, "_regenerate_conclusion", side_effect=self._legacy_conclusion):
            v2._regenerate_v2_conclusion(snapshot)
        headline = snapshot["conclusion"]["headline"]
        self.assertIn("A股ETF盘中主动买卖数据暂缺", headline)
        self.assertIn("ETF份额对应申赎资金大幅净流入12.6亿元", headline)
        self.assertIn("关注明日份额变化", headline)
        self.assertNotIn("申万一级和主题行业", headline)
        self.assertIn("半导体", snapshot["conclusion"]["facts"][2])

    def test_visible_sector_groups_are_exactly_the_client_industry_layer(self):
        snapshot = {"groups": self._groups() + [{"id": "growth", "name": "成长", "kind": "style", "flow1d": 2.0}]}
        sectors = v2._visible_sector_groups(snapshot)
        self.assertEqual({g["name"] for g in sectors}, {"传媒", "芯片", "半导体"})
        self.assertEqual(min(sectors, key=lambda g: g["flow1d"])["name"], "半导体")


if __name__ == "__main__":
    unittest.main()
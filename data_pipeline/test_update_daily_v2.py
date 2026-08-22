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

    def test_market_flow_headline_covers_strength_direction_and_divergence(self):
        cases = [
            (94.5, -185.7, 2500.0, 27455.95, "A股ETF盘中买盘偏强，主动买入净额94.5亿元；但ETF份额对应申赎资金大幅净流出185.7亿元\n—— 盘中承接偏强但份额净流出，交易改善尚未获得申购确认，暂偏存量资金博弈。下一交易日重点看份额能否转正，以确认盘中承接是否获得增量资金配合。"),
            (20.0, 20.0, 2000.0, 20000.0, "A股ETF盘中买盘小幅偏强，主动买入净额20.0亿元；ETF份额对应申赎资金小幅净流入20.0亿元\n—— 当日盘中与份额端同步改善，但整体力度有限，ETF资金行为仅边际回暖。下一交易日重点看份额增量能否延续，以及盘中买盘是否保持。"),
            (150.0, 120.0, 2000.0, 20000.0, "A股ETF盘中买盘明显占优，主动买入净额150.0亿元；ETF份额对应申赎资金大幅净流入120.0亿元\n—— 当日盘中与份额端同步明显改善，ETF增量资金信号较强。下一交易日重点看份额增量能否延续，以及盘中买盘是否保持。"),
            (-150.0, -120.0, 2000.0, 20000.0, "A股ETF盘中卖盘明显占优，主动卖出净额150.0亿元；ETF份额对应申赎资金大幅净流出120.0亿元\n—— 当日盘中与份额端同步明显承压，ETF资金行为短线显著趋谨慎。下一交易日重点看份额流出是否收窄，以及盘中卖压能否缓和。"),
            (-46.4, -31.8, 1525.63, 26701.71, "A股ETF盘中卖盘偏强，主动卖出净额46.4亿元；ETF份额对应申赎资金小幅净流出31.8亿元\n—— 当日盘中与份额端同步偏弱，但份额流出幅度有限，ETF资金行为仅边际趋谨慎。下一交易日重点看份额流出是否收窄，以及盘中卖压能否缓和。"),
            (-80.0, 80.0, 2000.0, 20000.0, "A股ETF盘中卖盘偏强，主动卖出净额80.0亿元；但ETF份额对应申赎资金明显净流入80.0亿元\n—— 盘中卖压偏强但份额净流入，回调承接较为明确，交易情绪与申购行为分化。下一交易日重点看盘中卖压能否缓和，以及份额承接能否延续。"),
            (10.0, -120.0, 2000.0, 20000.0, "A股ETF盘中买卖力量基本均衡；ETF份额对应申赎资金大幅净流出120.0亿元\n—— 盘中交易相对均衡，份额端净流出成为当日主要方向信号。下一交易日重点看份额流出是否收窄，以及盘中卖压是否抬升。"),
            (80.0, 5.0, 2000.0, 20000.0, "A股ETF盘中买盘偏强，主动买入净额80.0亿元；ETF份额对应申赎资金基本持平\n—— 盘中买盘偏强，但份额端接近平衡，交易情绪尚未转化为明确申赎方向。下一交易日重点看份额端能否转为净流入，以确认盘中买盘是否获得增量资金配合。"),
            (None, -120.0, None, 20000.0, "A股ETF盘中主动买卖数据暂缺；ETF份额对应申赎资金大幅净流出120.0亿元\n—— 仅从份额端看，资金净流出幅度较大。下一交易日重点看份额流出是否收窄，并补充验证盘中交易方向。"),
        ]
        for trade_value, primary_value, turnover, aum, expected in cases:
            with self.subTest(trade_value=trade_value, primary_value=primary_value):
                self.assertEqual(v2._market_flow_headline(trade_value, primary_value, turnover, aum), expected)

    def test_market_flow_headline_calibrates_every_strength_and_direction_regime(self):
        trade_cases = [
            ("balanced", 0.0, 2000.0),
            ("small_in", 20.0, 2000.0),
            ("small_out", -20.0, 2000.0),
            ("clear_in", 80.0, 2000.0),
            ("clear_out", -80.0, 2000.0),
            ("large_in", 150.0, 2000.0),
            ("large_out", -150.0, 2000.0),
            ("generic_in", 20.0, None),
            ("generic_out", -20.0, None),
        ]
        primary_cases = [
            ("flat", 0.0, 20000.0),
            ("small_in", 20.0, 20000.0),
            ("small_out", -20.0, 20000.0),
            ("clear_in", 60.0, 20000.0),
            ("clear_out", -60.0, 20000.0),
            ("large_in", 120.0, 20000.0),
            ("large_out", -120.0, 20000.0),
            ("generic_in", 20.0, None),
            ("generic_out", -20.0, None),
        ]
        prohibited = ("资金离场", "持续撤离", "有资金借反弹离场", "护盘", "股灾", "连续赎回", "资金出逃")
        for trade_name, trade_value, turnover in trade_cases:
            for primary_name, primary_value, aum in primary_cases:
                with self.subTest(trade=trade_name, primary=primary_name):
                    headline = v2._market_flow_headline(trade_value, primary_value, turnover, aum)
                    self.assertIn("\n—— ", headline)
                    self.assertFalse(any(text in headline for text in prohibited))
                    self.assertIn("下一交易日", headline)
                    if primary_name.startswith("small"):
                        self.assertIn("小幅", headline)
                        self.assertFalse(any(text in headline for text in ("大额", "明显转弱", "连续赎回")))
                    if "generic" in trade_name or "generic" in primary_name:
                        self.assertIn("缺少可比规模基准", headline)

        for primary_name, primary_value, aum in primary_cases:
            with self.subTest(trade="missing", primary=primary_name):
                headline = v2._market_flow_headline(None, primary_value, None, aum)
                self.assertIn("\n—— ", headline)
                self.assertFalse(any(text in headline for text in prohibited))
                if primary_name.startswith("small"):
                    self.assertIn("单日变动有限", headline)
                if primary_name.startswith("generic"):
                    self.assertIn("缺少可比规模基准", headline)

    def test_market_flow_headline_uses_five_day_context_without_overstatement(self):
        cases = [
            (-39.34, -99.7, 1320.8, 26683.68, 89.88, "当前更接近短期降温，尚不足以判断趋势转弱"),
            (39.34, 99.7, 1320.8, 26683.68, -89.88, "当前更接近短线修复，尚未形成趋势反转证据"),
            (39.34, 99.7, 1320.8, 26683.68, 89.88, "当前信号获得中短期方向支持"),
            (-39.34, -99.7, 1320.8, 26683.68, -89.88, "当前偏弱信号获得中短期方向印证"),
            (39.34, 99.7, 1320.8, 26683.68, 0.0, "近5个交易日份额端点接近平衡"),
        ]
        for trade_value, primary_value, turnover, aum, primary_5d, expected in cases:
            with self.subTest(primary_value=primary_value, primary_5d=primary_5d):
                headline = v2._market_flow_headline(
                    trade_value, primary_value, turnover, aum, primary_5d
                )
                self.assertIn(expected, headline)
                self.assertIn("下一交易日", headline)
                self.assertNotIn("连续赎回", headline)

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
        self.assertTrue(headline.startswith("A股ETF盘中买盘偏强，主动买入净额198.4亿元；但ETF份额对应申赎资金大幅净流出48.3亿元\n—— 盘中承接偏强但份额净流出，交易改善尚未获得申购确认，暂偏存量资金博弈。但近5个交易日份额端点仍为净流入，当前更接近短期降温，尚不足以判断趋势转弱。下一交易日重点看份额能否转正，以确认盘中承接是否获得增量资金配合。"))
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
        self.assertIn("近5个交易日份额端点同样为净流入，当前信号获得中短期方向支持。", headline)
        self.assertIn("下一交易日重点看份额增量能否延续", headline)
        self.assertNotIn("申万一级和主题行业", headline)
        self.assertIn("半导体", snapshot["conclusion"]["facts"][2])

    def test_visible_sector_groups_are_exactly_the_client_industry_layer(self):
        snapshot = {"groups": self._groups() + [{"id": "growth", "name": "成长", "kind": "style", "flow1d": 2.0}]}
        sectors = v2._visible_sector_groups(snapshot)
        self.assertEqual({g["name"] for g in sectors}, {"传媒", "芯片", "半导体"})
        self.assertEqual(min(sectors, key=lambda g: g["flow1d"])["name"], "半导体")


if __name__ == "__main__":
    unittest.main()
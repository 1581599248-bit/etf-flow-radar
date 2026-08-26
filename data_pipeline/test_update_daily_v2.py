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
        self.assertEqual(v2._trade_strength(200.0, 2000.0), "extreme")
        self.assertEqual(v2._primary_strength(9.9, 20000.0), "flat")
        self.assertEqual(v2._primary_strength(10.0, 20000.0), "small")
        self.assertEqual(v2._primary_strength(40.0, 20000.0), "clear")
        self.assertEqual(v2._primary_strength(100.0, 20000.0), "large")
        self.assertEqual(v2._primary_strength(200.0, 20000.0), "extreme")

    def test_finalized_current_market_copy_is_exact_and_share_led(self):
        headline = v2._market_flow_headline(
            -39.3,
            -99.7,
            1320.8,
            26683.68,
            inflow_text="资金份额流入居前为半导体与创新药。",
            allocation_state="concentrated_two_growth",
            allocation_tilt="growth",
        )
        conclusion = headline.split("\n—— ", 1)[1]
        self.assertEqual(
            conclusion,
            "份额净赎回偏多，盘中卖压同步但相对有限。"
            "资金份额流入居前为半导体与创新药。"
            "盘中卖压与份额赎回同步，市场交易与份额端均偏谨慎。",
        )

    def test_balanced_intraday_copy_always_keeps_the_net_amount(self):
        self.assertEqual(
            v2._trade_copy(1.72, "balanced"),
            "A股ETF盘中买卖力量基本均衡，主动买入净额1.7亿元",
        )
        self.assertEqual(
            v2._trade_copy(-1.72, "balanced"),
            "A股ETF盘中买卖力量基本均衡，主动卖出净额1.7亿元",
        )
        self.assertEqual(
            v2._trade_copy(0.0, "balanced"),
            "A股ETF盘中买卖力量基本均衡，主动买卖净额0.0亿元",
        )

    def test_market_flow_headline_covers_direction_strength_and_stays_concise(self):
        prohibited = (
            "资金离场", "持续撤离", "股灾", "连续赎回", "资金出逃",
            "下一交易日", "关注明日", "前5日", "前20日",
        )
        for trade in (None, -240.0, -160.0, -80.0, -40.0, 0.0, 40.0, 80.0, 160.0, 240.0):
            for primary in (-240.0, -140.0, -60.0, -20.0, 0.0, 20.0, 60.0, 140.0, 240.0):
                headline = v2._market_flow_headline(
                    trade,
                    primary,
                    2000.0 if trade is not None else None,
                    20000.0,
                    -100.0,
                    -200.0,
                    inflow_text="资金流入较为分散。",
                    allocation_state="dispersed",
                    allocation_tilt="mixed",
                )
                self.assertIn("\n—— ", headline)
                self.assertFalse(any(text in headline for text in prohibited))
                self.assertTrue(headline.endswith("。"))
                if trade is not None:
                    self.assertRegex(headline.split("；", 1)[0], r"净额\d+\.\d亿元")

    def test_historical_values_do_not_reenter_the_daily_conclusion(self):
        headline = v2._market_flow_headline(
            -39.3,
            -99.7,
            1320.8,
            26683.68,
            126.5,
            500.0,
            inflow_text="资金份额流入居前为半导体与创新药。",
            allocation_state="concentrated_two_growth",
            allocation_tilt="growth",
        )
        self.assertNotIn("前5日", headline)
        self.assertNotIn("前20日", headline)
        self.assertNotIn("下一交易日", headline)
        self.assertEqual(headline.count("99.7亿元"), 1)

    def test_current_regime_copy_preserves_finalized_order_and_relationships(self):
        share_led_outflow = v2._current_regime_copy(
            -30.0, -100.0, "small", "clear",
            "资金份额流入居前为半导体与创新药。", "concentrated_two_growth", "growth",
        )
        trade_led_outflow = v2._current_regime_copy(
            -150.0, -30.0, "large", "small",
            "资金流入有限。", "limited", "unknown",
        )
        share_led_inflow = v2._current_regime_copy(
            -30.0, 140.0, "small", "large",
            "资金份额流入居前为价值。", "concentrated_one_defensive", "defensive",
        )
        divergent = v2._current_regime_copy(
            150.0, -30.0, "large", "small",
            "资金流入较为分散。", "dispersed", "mixed",
        )

        self.assertTrue(share_led_outflow.startswith("份额净赎回偏多"))
        self.assertIn("盘中卖压同步但相对有限", share_led_outflow)
        self.assertTrue(share_led_outflow.endswith("盘中卖压与份额赎回同步，市场交易与份额端均偏谨慎。"))
        self.assertTrue(trade_led_outflow.startswith("份额少量净赎回"))
        self.assertIn("盘中卖压同步且更强", trade_led_outflow)
        self.assertTrue(trade_led_outflow.endswith("盘中卖压与份额赎回同步，市场交易与份额端均偏谨慎。"))
        self.assertIn("盘中卖压背离但相对有限", share_led_inflow)
        self.assertTrue(share_led_inflow.endswith("市场总体流向分化，防御方向获得资金申购承接。"))
        self.assertIn("盘中买盘背离且更强", divergent)
        self.assertTrue(divergent.endswith("市场总体流向分化，盘中买盘尚未转化为整体份额申购。"))

    def test_merged_broad_and_sector_ranking_always_names_top_two_inflows(self):
        state, text, tilt = v2._inflow_focus_context({"groups": [
            {"name": "红利低波", "kind": "style", "flow1d": 100.0},
            {"name": "沪深300", "kind": "broad", "flow1d": 8.0},
            {"name": "半导体", "kind": "industry", "flow1d": 12.0},
            {"name": "创新药", "kind": "industry", "flow1d": 6.0},
            {"name": "中证500", "kind": "broad", "flow1d": 4.0},
        ]})
        self.assertEqual(state, "concentrated_two_mixed")
        self.assertEqual(text, "资金份额流入居前为半导体与沪深300。")
        self.assertEqual(tilt, "mixed")

        state, text, tilt = v2._inflow_focus_context({"groups": [
            {"name": "沪深300", "kind": "broad", "flow1d": 2.0},
            {"name": "创新药", "kind": "industry", "flow1d": 1.0},
            {"name": "半导体", "kind": "industry", "flow1d": 1.0},
        ]})
        self.assertEqual(text, "资金份额流入居前为沪深300与创新药。")
        self.assertNotEqual(state, "dispersed")

        self.assertEqual(
            v2._inflow_focus_context({"groups": [
                {"name": "沪深300", "kind": "broad", "flow1d": 0.0},
                {"name": "半导体", "kind": "industry", "flow1d": -1.0},
            ]}),
            ("no_inflow", "未出现明确资金份额净流入方向。", "unknown"),
        )

    def test_divergent_growth_broad_flows_describe_targeted_primary_support(self):
        headline = v2._market_flow_headline(
            -114.5,
            107.5,
            1552.51,
            26368.23,
            inflow_text="资金份额流入居前为科创50与创业板指。",
            allocation_state="concentrated_two_growth",
            allocation_tilt="growth",
            allocation_scope="broad",
        )
        self.assertTrue(headline.endswith("市场总体流向分化，成长宽基获得资金申购承接。"))

    def test_market_conclusion_explains_all_trade_share_combinations(self):
        self.assertEqual(
            v2._market_conclusion_copy(
                100.0, "clear", "concentrated_two_growth", "growth",
                trade_value=120.0, trade_strength="clear", allocation_scope="industry",
            ),
            "盘中买盘与份额申购共振，成长板块的增量配置意愿较强。",
        )
        self.assertEqual(
            v2._market_conclusion_copy(
                -100.0, "clear", "concentrated_two_growth", "growth",
                trade_value=-120.0, trade_strength="clear", allocation_scope="broad",
            ),
            "盘中卖压与份额赎回同步，市场交易与份额端均偏谨慎。",
        )
        self.assertEqual(
            v2._market_conclusion_copy(
                -100.0, "clear", "concentrated_two_growth", "growth",
                trade_value=120.0, trade_strength="clear", allocation_scope="mixed",
            ),
            "市场总体流向分化，盘中买盘尚未转化为整体份额申购。",
        )
        self.assertEqual(
            v2._market_conclusion_copy(
                100.0, "clear", "concentrated_two_growth", "growth",
                trade_value=None, trade_strength=None, allocation_scope="broad",
            ),
            "成长宽基获得增量配置，盘中交易信号暂缺。",
        )
        self.assertEqual(
            v2._market_conclusion_copy(
                100.0, "clear", "concentrated_two_growth", "growth",
                trade_value=0.0, trade_strength="balanced", allocation_scope="broad",
            ),
            "成长宽基获得增量配置，盘中买卖未形成单边方向。",
        )
        self.assertEqual(
            v2._market_conclusion_copy(
                -100.0, "clear", "limited", "unknown",
                trade_value=0.0, trade_strength="balanced", allocation_scope="unknown",
            ),
            "份额端以净赎回为主，盘中买卖未形成单边方向。",
        )
        self.assertEqual(
            v2._market_conclusion_copy(
                0.0, "flat", "limited", "unknown",
                trade_value=120.0, trade_strength="clear", allocation_scope="unknown",
            ),
            "盘中买盘占优，但尚未形成明确的份额申购。",
        )
        self.assertEqual(
            v2._market_conclusion_copy(
                0.0, "flat", "limited", "unknown",
                trade_value=-120.0, trade_strength="clear", allocation_scope="unknown",
            ),
            "盘中卖压占优，但尚未形成明确的份额赎回。",
        )
        self.assertEqual(
            v2._market_conclusion_copy(
                0.0, "flat", "limited", "unknown",
                trade_value=0.0, trade_strength="balanced", allocation_scope="unknown",
            ),
            "盘中与份额端均未形成明确方向，市场以存量博弈为主。",
        )

    def test_all_6864_structural_scenarios_are_composable(self):
        primary_cases = [
            (0.0, 20000.0),
            (20.0, 20000.0), (-20.0, 20000.0),
            (60.0, 20000.0), (-60.0, 20000.0),
            (140.0, 20000.0), (-140.0, 20000.0),
            (240.0, 20000.0), (-240.0, 20000.0),
            (20.0, None), (-20.0, None),
        ]
        trade_cases = [
            (None, None),
            (0.0, 2000.0),
            (40.0, 2000.0), (-40.0, 2000.0),
            (80.0, 2000.0), (-80.0, 2000.0),
            (160.0, 2000.0), (-160.0, 2000.0),
            (240.0, 2000.0), (-240.0, 2000.0),
            (20.0, None), (-20.0, None),
        ]
        allocation_cases = [
            ("unavailable", "资金份额流向暂不明确。", "unknown"),
            ("no_inflow", "资金未见明显集中流入。", "unknown"),
            ("limited", "资金流入有限。", "unknown"),
            ("concentrated_one_growth", "资金份额流入居前为半导体。", "growth"),
            ("concentrated_two_growth", "资金份额流入居前为半导体与创新药。", "growth"),
            ("concentrated_one_defensive", "资金份额流入居前为红利低波。", "defensive"),
            ("concentrated_two_defensive", "资金份额流入居前为红利低波与价值。", "defensive"),
            ("concentrated_one_cyclical", "资金份额流入居前为有色金属。", "cyclical"),
            ("concentrated_two_cyclical", "资金份额流入居前为有色金属与券商。", "cyclical"),
            ("concentrated_one_neutral", "资金份额流入居前为综合。", "neutral"),
            ("concentrated_two_neutral", "资金份额流入居前为综合与保险。", "neutral"),
            ("concentrated_two_mixed", "资金份额流入居前为半导体与红利低波。", "mixed"),
            ("dispersed", "资金流入较为分散。", "mixed"),
        ]
        allocation_scopes = ("unknown", "broad", "industry", "mixed")
        generated = 0
        for primary_value, aum in primary_cases:
            for trade_value, turnover in trade_cases:
                for allocation_state, inflow_text, allocation_tilt in allocation_cases:
                    for allocation_scope in allocation_scopes:
                        headline = v2._market_flow_headline(
                            trade_value,
                            primary_value,
                            turnover,
                            aum,
                            inflow_text=inflow_text,
                            allocation_state=allocation_state,
                            allocation_tilt=allocation_tilt,
                            allocation_scope=allocation_scope,
                        )
                        conclusion = headline.split("\n—— ", 1)[1]
                        self.assertTrue(conclusion.startswith("份额"))
                        self.assertEqual(conclusion.count("。"), 3)
                        self.assertNotIn("市场总体扩张", conclusion)
                        self.assertNotIn("市场总体收缩", conclusion)
                        self.assertTrue(conclusion.endswith("。"))
                        generated += 1
        self.assertEqual(generated, 6864)
        self.assertEqual(generated, v2.CONCLUSION_SCENARIO_COUNT)

    def test_prior_history_excludes_current_and_unstable_universe(self):
        with tempfile.TemporaryDirectory() as tmp:
            public = Path(tmp)
            history = public / "history"
            history.mkdir()
            for offset, flow in enumerate((10.0, 20.0, 30.0, 40.0, 50.0), start=1):
                payload = {
                    "status": "verified",
                    "tradeDate": f"2026-08-0{offset}",
                    "market": {"flow1d": flow, "etfCount": 100},
                }
                (history / f"{payload['tradeDate']}.json").write_text(json.dumps(payload), "utf-8")
            unstable = {"status": "verified", "tradeDate": "2026-08-07", "market": {"flow1d": 999.0, "etfCount": 90}}
            (history / "2026-08-07.json").write_text(json.dumps(unstable), "utf-8")
            current = {"status": "verified", "tradeDate": "2026-08-10", "market": {"flow1d": 888.0, "etfCount": 100}}
            (history / "2026-08-10.json").write_text(json.dumps(current), "utf-8")
            with patch.object(v2.base, "PUBLIC", public):
                flows = v2._prior_primary_flows(date(2026, 8, 10), 100)
        self.assertEqual(flows, [10.0, 20.0, 30.0, 40.0, 50.0])
        self.assertEqual(v2._prior_window_total(flows, 5), 150.0)

    def test_homepage_headline_uses_share_trade_merged_focus_market_order(self):
        snapshot = {
            "market": {
                "flow1d": -48.3,
                "aum": 5000.0,
                "increaseEtfCount1d": 231,
                "decreaseEtfCount1d": 409,
                "unchangedEtfCount1d": 607,
                "flow5dEndpoint": 20.0,
            },
            "groups": [
                {"id": "hs300", "name": "沪深300", "kind": "broad", "flow1d": -3.0},
                {"id": "csi500", "name": "中证500", "kind": "broad", "flow1d": 1.0},
                {"id": "semi", "name": "半导体", "kind": "industry", "flow1d": 12.6},
                {"id": "innovative_drug", "name": "创新药", "kind": "industry", "flow1d": 3.8},
                {"id": "ai_compute", "name": "AI算力", "kind": "industry", "flow1d": 3.0},
                {"id": "dividend_lowvol", "name": "红利低波", "kind": "style", "flow1d": 1.7},
                {"id": "value", "name": "价值", "kind": "style", "flow1d": 1.2},
            ],
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
        self.assertTrue(headline.startswith("A股ETF盘中"))
        self.assertIn(
            "\n—— 份额大量净赎回，盘中买盘背离但相对有限。"
            "资金份额流入居前为半导体与创新药。"
            "市场总体流向分化，盘中买盘尚未转化为整体份额申购。",
            headline,
        )
        self.assertNotIn("红利低波与价值", headline)
        self.assertNotIn("前5日", headline)
        self.assertNotIn("下一交易日", headline)
        self.assertNotIn("A股股票ETF当日合计", headline)
        self.assertIn("净流入居前为半导体+12.6亿、创新药+3.8亿", snapshot["conclusion"]["facts"][2])
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
        self.assertIn("份额大量净申购，盘中数据暂缺", headline)
        self.assertIn("资金份额流入居前为传媒与中证500。", headline)
        self.assertIn("份额端出现净申购，盘中交易信号暂缺。", headline)
        self.assertNotIn("申万一级和主题行业", headline)
        self.assertIn("半导体", snapshot["conclusion"]["facts"][2])

    def test_visible_sector_groups_are_exactly_the_client_industry_layer(self):
        snapshot = {"groups": self._groups() + [{"id": "growth", "name": "成长", "kind": "style", "flow1d": 2.0}]}
        sectors = v2._visible_sector_groups(snapshot)
        self.assertEqual({g["name"] for g in sectors}, {"传媒", "芯片", "半导体"})
        self.assertEqual(min(sectors, key=lambda g: g["flow1d"])["name"], "半导体")


if __name__ == "__main__":
    unittest.main()

import copy
import itertools
import json
from pathlib import Path
import unittest

import conclusion_market as cm
import update_daily_v2 as v2


def group(name, kind, flow):
    return {"name": name, "kind": kind, "flow1d": flow}


G = group
STRUCTURES = [
    None, [], [G("沪深300", "broad", 0)],
    [G("科创50", "broad", 50)],
    [G("红利", "style", -50)],
    [G("科创50", "broad", 50), G("中证1000", "broad", 30), G("券商", "industry", -10), G("红利", "style", -8)],
    [G("沪深300", "broad", 50), G("中证2000", "broad", 30), G("煤炭", "industry", -10)],
    [G("成长", "style", 50), G("价值", "style", 30), G("红利", "style", -10)],
    [G("半导体", "industry", 50), G("创新药", "industry", 30), G("银行", "industry", -10)],
    [G("科创50", "broad", 50), G("红利", "style", 30), G("券商", "industry", -10)],
    [G("沪深300", "broad", 50), G("半导体", "industry", 30), G("煤炭", "industry", -10)],
    [G("红利", "style", 50), G("创新药", "industry", 30), G("中证500", "broad", -10)],
    [G("科创50", "broad", 50), G("半导体", "industry", -30)],
    [G("中证1000", "broad", 50), G("中证500", "broad", -30)],
    [G("红利", "style", 50), G("红利低波", "style", -30)],
    [G("科创50", "broad", 50), G("成长", "style", -30), G("半导体", "industry", -10)],
    [G("券商", "industry", 50), G("银行", "industry", -30), G("红利", "style", -10)],
    [G("未知主题", "industry", 50), G("未知风格", "style", -30)],
    [G("科创50", "broad", 50), G("红利", "style", -0.001)],
    [G("科创50", "broad", 0.001), G("红利", "style", -50)],
]
PRIMARY_CASES = [(0, 20000)] + [(sign * value, 20000) for value in (20, 60, 140, 240) for sign in (1, -1)] + [(20, None), (-20, None)]
TRADE_CASES = [(None, None), (0, 2000)] + [(sign * value, 2000) for value in (40, 80, 160, 240) for sign in (1, -1)] + [(20, None), (-20, None)]
SCALES = (0.01, 0.2, 0.8, 2.0, 5.0)


class ConclusionMarketTests(unittest.TestCase):
    def test_all_twelve_market_relationships(self):
        expected = {
            (1, 1): "市场资金偏向增配", (-1, -1): "市场资金偏向收缩",
            (1, -1): "市场资金流向分化", (-1, 1): "市场资金流向分化",
            (1, 0): "市场配置端偏向增配", (-1, 0): "市场配置端偏向收缩",
            (0, 1): "市场偏交易性买入", (0, -1): "市场偏交易性卖出",
            (0, 0): "市场资金方向暂不明朗",
        }
        for p, t in itertools.product((-1, 0, 1), (None, -1, 0, 1)):
            actual = cm.market_state(p, "flat" if p == 0 else "clear", t, "balanced" if t == 0 else "clear")
            self.assertEqual(actual, "市场风向暂缺交易端确认" if t is None else expected[p, t])

    def test_actual_snapshot_approved_copy_and_numerics_unchanged(self):
        path = Path(__file__).resolve().parents[1] / "site/data/history/2026-09-04.json"
        original = json.loads(path.read_text())
        updated = copy.deepcopy(original)
        v2._regenerate_v2_conclusion(updated)
        self.assertTrue(updated["conclusion"]["headline"].endswith(
            "市场配置大幅扩张，资金大幅承接成长与中小盘，"
            "金融与高股息小幅降温；盘中卖压未转化为整体赎回。"
        ))
        self.assertEqual({k: v for k, v in original.items() if k != "conclusion"},
                         {k: v for k, v in updated.items() if k != "conclusion"})
        self.assertIn("科创50与中证1000，流出居前为券商与红利", updated["conclusion"]["headline"])

    def test_classification_does_not_confuse_growth_finance_and_dividends(self):
        for name, kind, label in (
            ("成长", "style", "成长风格"), ("科创50", "broad", "科技成长"),
            ("中证1000", "broad", "中小盘"), ("沪深300", "broad", "大盘宽基"),
            ("红利低波", "style", "高股息"), ("价值", "style", "价值质量"),
            ("银行", "industry", "金融"), ("煤炭", "industry", "资源周期"),
            ("创新药", "industry", "医药医疗"), ("金融科技", "industry", "金融"),
            ("未知主题", "industry", "其他行业"),
        ):
            self.assertEqual(cm.direction(G(name, kind, 1)), label)

    def test_existing_comparable_history_preserves_source_facts(self):
        root = Path(__file__).resolve().parents[1] / "site/data/history"
        tested = 0
        for path in sorted(root.glob("*.json")):
            original = json.loads(path.read_text())
            # 2026-08-11 is a pre-market-schema archive, not a comparable input.
            if "market" not in original:
                continue
            updated = copy.deepcopy(original)
            v2._regenerate_v2_conclusion(updated)
            self.assertEqual({k: v for k, v in original.items() if k != "conclusion"},
                             {k: v for k, v in updated.items() if k != "conclusion"})
            final = updated["conclusion"]["headline"].split("。")[-2]
            self.assertLessEqual(len(final), 82)
            self.assertNotIn("高低切换", final)
            tested += 1
        self.assertGreaterEqual(tested, 18)

    def test_latest_primary_recomputes_from_unique_etf_facts(self):
        path = Path(__file__).resolve().parents[1] / "site/data/latest.json"
        snapshot = json.loads(path.read_text())
        rows = snapshot["etfs"]
        self.assertEqual(len({e["code"] for e in rows}), len(rows))
        self.assertEqual(len(rows), snapshot["market"]["etfCount"])
        recomputed = sum(e["shareDelta1d"] * e["nav"] / 1e8 for e in rows)
        self.assertAlmostEqual(recomputed, snapshot["market"]["flow1d"], delta=0.0051)
        for e in rows:
            self.assertAlmostEqual(e["shareDelta1d"] * e["nav"] / 1e8, e["flow1d"], delta=0.0051)

    def test_amount_and_dispersion_guards_do_not_suppress_rank(self):
        groups = STRUCTURES[18]
        text = cm.render_market(49.999, "clear", -30, "small", groups, 20000)
        self.assertIn("高股息略有降温", text)
        self.assertIn("红利", v2._outflow_focus_context({"groups": groups})[1])
        dispersed = [G(name, kind, 10) for name, kind in (
            ("科创50", "broad"), ("中证1000", "broad"), ("沪深300", "broad"),
            ("红利", "style"), ("银行", "industry"), ("煤炭", "industry"),
        )]
        self.assertIn("资金承接中小盘与大盘宽基", cm.render_market(60, "clear", 40, "small", dispersed))
        self.assertIn("资金份额流入居前为", v2._inflow_focus_context({"groups": dispersed})[1])
        # Small absolute flows are not called negligible when they are all activity.
        self.assertEqual(cm.side_context([G("红利", "style", -0.01)], -1, 20000)["magnitude"], "limited")

    def test_direction_magnitude_uses_market_aum_and_exact_boundaries(self):
        self.assertEqual(cm.magnitude(0, 20000), "flat")
        self.assertEqual(cm.magnitude(9.999, 20000), "limited")
        self.assertEqual(cm.magnitude(10, 20000), "small")
        self.assertEqual(cm.magnitude(39.999, 20000), "small")
        self.assertEqual(cm.magnitude(40, 20000), "clear")
        self.assertEqual(cm.magnitude(99.999, 20000), "clear")
        self.assertEqual(cm.magnitude(100, 20000), "large")
        self.assertEqual(cm.magnitude(199.999, 20000), "large")
        self.assertEqual(cm.magnitude(200, 20000), "extreme")
        self.assertEqual(cm.magnitude(20, None), "generic")

    def test_market_posture_uses_total_share_flow_not_ranked_direction_type(self):
        aggressive = cm.side_context([
            G("半导体", "industry", 60), G("创新药", "industry", 40),
            G("沪深300", "broad", 10),
        ], 1, 20000)
        defensive = cm.side_context([
            G("红利低波", "style", 60), G("价值", "style", 40),
            G("沪深300", "broad", 10),
        ], 1, 20000)
        mixed = cm.side_context([
            G("科创50", "broad", 60), G("红利", "style", 40),
            G("沪深300", "broad", 10),
        ], 1, 20000)
        dispersed = cm.side_context([
            G("科创50", "broad", 10), G("中证1000", "broad", 10),
            G("沪深300", "broad", 10), G("红利", "style", 10),
            G("券商", "industry", 10), G("煤炭", "industry", 10),
        ], 1, 20000)
        self.assertEqual(cm.market_posture(100, "clear", aggressive), "市场配置明显扩张")
        self.assertEqual(cm.market_posture(100, "clear", defensive), "市场配置明显扩张")
        self.assertEqual(cm.market_posture(100, "clear", mixed), "市场配置明显扩张")
        self.assertEqual(cm.market_posture(100, "clear", dispersed), "市场配置明显扩张")
        self.assertEqual(cm.market_posture(-100, "clear", aggressive), "市场配置整体偏谨慎")

    def test_small_redemption_keeps_total_caution_while_showing_structural_inflows(self):
        """Net share-flow posture must not be overwritten by the destination mix."""
        groups = [
            G("科创50", "broad", 10.9), G("券商", "industry", 3.0),
            G("沪深300", "broad", -15.9), G("中证A500", "broad", -12.2),
        ]
        self.assertEqual(cm.total_allocation_posture(-25.99, "small"), "市场配置略偏谨慎")
        self.assertEqual(
            cm.render_market(-25.99, "small", -66.72, "clear", groups, 26655.42),
            "市场配置略偏谨慎，资金小幅承接成长与金融，大盘宽基小幅降温。",
        )

    def test_current_direction_amounts_use_all_matching_groups(self):
        path = Path(__file__).resolve().parents[1] / "site/data/history/2026-09-04.json"
        snapshot = json.loads(path.read_text())
        rows = cm.eligible_groups(snapshot["groups"])
        incoming = cm.side_context(rows, 1, snapshot["market"]["aum"])
        outgoing = cm.side_context(rows, -1, snapshot["market"]["aum"])
        self.assertEqual(incoming["labels"], ["科技成长", "中小盘"])
        self.assertAlmostEqual(incoming["represented"], 142.65, places=2)
        self.assertAlmostEqual(incoming["share"], 0.68395, places=4)
        self.assertEqual(incoming["magnitude"], "large")
        self.assertEqual(outgoing["labels"], ["金融", "高股息"])
        self.assertAlmostEqual(outgoing["represented"], 21.63, places=2)
        self.assertAlmostEqual(outgoing["share"], 0.43679, places=4)
        self.assertEqual(outgoing["magnitude"], "small")

    def test_shared_direction_is_not_evidence_of_high_low_rotation(self):
        text = cm.render_market(20, "small", 40, "small", STRUCTURES[12])
        self.assertEqual(text, "市场配置小幅扩张，成长内部申赎分化。")
        self.assertNotIn("高低", text)

    def test_growth_inflow_and_growth_outflow_are_never_called_one_way_aggressive(self):
        """A 科创50 subscription cannot erase a simultaneous semiconductor redemption."""
        groups = [
            G("科创50", "broad", 13.59), G("成长", "style", 5.20),
            G("半导体", "industry", -4.69), G("上证50", "broad", -3.88),
        ]
        text = cm.render_market(26.43, "small", -33.82, "small", groups, 26484.50)
        self.assertEqual(
            text,
            "市场配置小幅扩张，成长内部申赎分化，大盘宽基略有降温；"
            "盘中卖压未转化为整体赎回。",
        )
        self.assertNotIn("偏进攻", text)
        self.assertNotIn("成长略有降温", text)

    def test_same_side_signals_do_not_append_a_redundant_relationship_close(self):
        cases = (
            (20, "small", 40, "small", "配置与交易形成同向支撑"),
            (-20, "small", -40, "small", "配置与交易共同偏谨慎"),
            (0, "flat", 0, "balanced", "配置与交易均缺乏明确方向"),
        )
        for primary, primary_strength, trade, trade_strength, redundant in cases:
            text = cm.render_market(primary, primary_strength, trade, trade_strength, STRUCTURES[5])
            self.assertNotIn(redundant, text)
            self.assertTrue(text.endswith("。"))

    def test_invalid_or_duplicate_data_fails_closed(self):
        for bad in (None, float("nan"), float("inf"), True, "3"):
            with self.assertRaises(ValueError):
                cm.render_market(1, "small", 1, "small", [G("红利", "style", bad)])
        with self.assertRaises(ValueError):
            cm.eligible_groups([G("红利", "style", 1), G("红利", "style", 2)])

    def test_parent_rollups_excluded_and_ties_stable(self):
        rows = [G("半导体", "industry", 10), G("科创50", "broad", 10), G("红利", "style", 10)]
        first = v2._inflow_focus_context({"groups": rows})
        self.assertEqual(first, v2._inflow_focus_context({"groups": list(reversed(rows))}))
        self.assertEqual(first, v2._inflow_focus_context({"groups": rows, "industryRollups": [G("电子", "industry", 1000)]}))

    def test_13200_production_headline_input_combinations(self):
        """Input/format cross-product, NOT 13,200 distinct market stories."""
        count = 0
        for (p, aum), (t, turnover), structure, scale in itertools.product(PRIMARY_CASES, TRADE_CASES, STRUCTURES, SCALES):
            rows = None if structure is None else [dict(g, flow1d=g["flow1d"] * scale) for g in structure]
            snapshot = {"groups": rows or []}
            ins, itext, itilt = v2._inflow_focus_context(snapshot)
            outs, otext, otilt = v2._outflow_focus_context(snapshot)
            headline = v2._market_flow_headline(
                t, p, turnover, aum, inflow_text=itext, allocation_state=ins,
                allocation_tilt=itilt, outflow_text=otext, outflow_state=outs,
                outflow_tilt=otilt, direction_groups=rows,
            )
            fact, conclusion = headline.split("\n—— ")
            final = conclusion.split("。")[-2]
            self.assertEqual(conclusion.count("。"), 3)
            self.assertLessEqual(len(final), 82)
            self.assertNotRegex(final, r"高低切换|全面|持续|风险偏好回升|存量博弈|机构|转入|出逃")
            if t is not None:
                self.assertRegex(fact.split("；")[0], r"净额\d+\.\d亿元")
            if rows:
                names = [g["name"] for g in rows]
                for name in ("科创50", "中证1000", "沪深300", "中证2000", "红利低波", "半导体", "创新药", "券商"):
                    if name in names:
                        self.assertNotIn(name, final)
                if any(g["flow1d"] < 0 for g in rows):
                    self.assertRegex(final, "降温|流出|申赎分化")
            count += 1
        self.assertEqual(count, 11 * 12 * 20 * 5)
        self.assertEqual(count, 13200)


if __name__ == "__main__":
    unittest.main()

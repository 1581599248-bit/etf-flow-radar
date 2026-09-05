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
SCALES = (0.0001, 1, 100)


class ConclusionMarketTests(unittest.TestCase):
    def test_all_twelve_market_relationships(self):
        expected = {
            (1, 1): "市场资金偏向增配", (-1, -1): "市场资金偏向减配",
            (1, -1): "市场资金流向分化", (-1, 1): "市场资金流向分化",
            (1, 0): "市场配置端偏向增配", (-1, 0): "市场配置端偏向减配",
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
            "市场资金流向分化，配置偏向科技成长与中小盘，部分金融与高股息方向资金流出。"
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
            self.assertLessEqual(len(final), 72)
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
        self.assertIn("部分高股息方向小额流出", text)
        self.assertIn("红利", v2._outflow_focus_context({"groups": groups})[1])
        dispersed = [G(name, kind, 10) for name, kind in (
            ("科创50", "broad"), ("中证1000", "broad"), ("沪深300", "broad"),
            ("红利", "style"), ("银行", "industry"), ("煤炭", "industry"),
        )]
        self.assertIn("申购分布于多个方向", cm.render_market(60, "clear", 40, "small", dispersed))
        self.assertIn("资金份额流入居前为", v2._inflow_focus_context({"groups": dispersed})[1])
        # Small absolute flows are not called negligible when they are all activity.
        self.assertFalse(cm.side_context([G("红利", "style", -0.01)], -1, 20000)["small"])

    def test_shared_direction_is_not_evidence_of_high_low_rotation(self):
        text = cm.render_market(20, "small", 40, "small", STRUCTURES[12])
        self.assertEqual(text, "市场资金偏向增配，科技成长内部申赎分化。")
        self.assertNotIn("高低", text)

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

    def test_7920_production_headline_input_combinations(self):
        """Input/format cross-product, NOT 7,920 real markets or distinct stories."""
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
            self.assertLessEqual(len(final), 72)
            self.assertNotRegex(final, r"高低切换|全面|持续|风险偏好回升|存量博弈|机构|转入|出逃")
            if t is not None:
                self.assertRegex(fact.split("；")[0], r"净额\d+\.\d亿元")
            if rows:
                names = [g["name"] for g in rows]
                for name in ("科创50", "中证1000", "沪深300", "中证2000", "红利低波", "半导体", "创新药", "券商"):
                    if name in names:
                        self.assertNotIn(name, final)
                if any(g["flow1d"] < 0 for g in rows):
                    self.assertRegex(final, "流出|申赎分化")
            count += 1
        self.assertEqual(count, 11 * 12 * 20 * 3)
        self.assertEqual(count, 7920)


if __name__ == "__main__":
    unittest.main()

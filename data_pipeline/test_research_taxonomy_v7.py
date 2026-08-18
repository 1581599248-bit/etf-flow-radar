from __future__ import annotations

import unittest

import research_taxonomy_v7 as taxonomy


class ResearchTaxonomyV7Tests(unittest.TestCase):
    def test_broad_names_map_to_equally_broad_research_themes(self):
        cases = [
            ("510150", "消费ETF招商", "sw_food_beverage", "theme_consumer", "消费"),
            ("516890", "新材料ETF华夏", "sw_basic_chemicals", "theme_new_materials", "新材料"),
            ("159930", "能源ETF汇添富", "sw_petrochemical", "theme_energy", "能源"),
            ("516790", "高端制造ETF华夏", "sw_machinery", "theme_advanced_manufacturing", "高端制造"),
            ("516160", "新能源ETF南方", "sw_power_equipment", "theme_new_energy", "新能源"),
            ("561330", "矿业ETF国泰", "sw_nonferrous", "theme_mining", "矿业"),
        ]
        snapshot = {
            "etfs": [
                {"code": code, "name": name, "groupId": source, "groupName": source, "kind": "industry", "aum": float(i + 1)}
                for i, (code, name, source, _, _) in enumerate(cases)
            ],
            "universe": [
                {"code": code, "name": name, "groupId": source, "groupName": source, "kind": "industry"}
                for code, name, source, _, _ in cases
            ],
            "groups": [],
            "quality": {},
        }
        taxonomy.apply(snapshot)

        by_code = {row["code"]: row for row in snapshot["etfs"]}
        universe = {row["code"]: row for row in snapshot["universe"]}
        group_ids = {group["id"] for group in snapshot["groups"]}
        for code, _, _, target, name in cases:
            self.assertEqual(by_code[code]["groupId"], target)
            self.assertEqual(by_code[code]["groupName"], name)
            self.assertEqual(universe[code]["groupId"], target)
            self.assertIn(target, group_ids)
            self.assertEqual(by_code[code]["taxonomyRuleId"], target)
        self.assertEqual(snapshot["quality"]["broadThemeTaxonomyRemapCount"], len(cases))

    def test_specific_industry_names_are_not_broadened(self):
        rows = [
            {"code": "512690", "name": "酒ETF鹏华", "groupId": "sw_food_beverage", "kind": "industry", "aum": 10.0},
            {"code": "515790", "name": "光伏ETF华泰柏瑞", "groupId": "sw_power_equipment", "kind": "industry", "aum": 20.0},
            {"code": "512170", "name": "油气ETF华宝", "groupId": "sw_petrochemical", "kind": "industry", "aum": 5.0},
            {"code": "159869", "name": "游戏ETF华夏", "groupId": "media_game", "kind": "industry", "aum": 8.0},
        ]
        snapshot = {
            "etfs": [dict(row) for row in rows],
            "universe": [dict(row) for row in rows],
            "groups": [],
            "quality": {},
        }
        taxonomy.apply(snapshot)
        self.assertEqual([row["groupId"] for row in snapshot["etfs"]], [row["groupId"] for row in rows])
        self.assertEqual(snapshot["quality"]["broadThemeTaxonomyRemaps"], [])

    def test_taxonomy_never_changes_market_fact_fields(self):
        snapshot = {
            "etfs": [{
                "code": "510150", "name": "消费ETF招商", "groupId": "sw_food_beverage", "kind": "industry",
                "shareDelta1d": 123456.0, "nav": 1.2345, "flow1d": 0.01, "aum": 99.0,
            }],
            "universe": [{
                "code": "510150", "name": "消费ETF招商", "groupId": "sw_food_beverage", "kind": "industry",
                "shareDelta1d": 123456.0, "nav": 1.2345, "primaryFlow1d": 0.01,
            }],
            "groups": [],
            "quality": {},
        }
        before = {
            "etf": (snapshot["etfs"][0]["shareDelta1d"], snapshot["etfs"][0]["nav"], snapshot["etfs"][0]["flow1d"]),
            "universe": (snapshot["universe"][0]["shareDelta1d"], snapshot["universe"][0]["nav"], snapshot["universe"][0]["primaryFlow1d"]),
        }
        taxonomy.apply(snapshot)
        after = {
            "etf": (snapshot["etfs"][0]["shareDelta1d"], snapshot["etfs"][0]["nav"], snapshot["etfs"][0]["flow1d"]),
            "universe": (snapshot["universe"][0]["shareDelta1d"], snapshot["universe"][0]["nav"], snapshot["universe"][0]["primaryFlow1d"]),
        }
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()

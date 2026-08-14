import unittest

import update_daily_v2 as v2


class TaxonomyV2Tests(unittest.TestCase):
    def test_mainstream_themes(self):
        cases = {
            "机器人ETF华夏": ("theme", "机器人"),
            "新能源ETF易方达": ("theme", "新能源"),
            "酒ETF鹏华": ("theme", "白酒酒类"),
            "消费ETF华夏": ("theme", "消费"),
            "人工智能ETF华富": ("theme", "AI与算力"),
            "半导体设备ETF国泰": ("theme", "半导体"),
            "创新药ETF": ("theme", "创新药"),
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                item = v2.classify_etf_v2(name)
                self.assertIsNotNone(item)
                self.assertEqual((item["kind"], item["name"]), expected)

    def test_broad_index_has_priority(self):
        item = v2.classify_etf_v2("沪深300ETF华泰柏瑞")
        self.assertEqual(item["kind"], "broad")
        self.assertEqual(item["name"], "沪深300")

    def test_cross_border_is_excluded(self):
        self.assertIsNone(v2.classify_etf_v2("港股通消费ETF"))

    def test_display_name_prefers_full_name(self):
        self.assertEqual(v2._display_name({"name": "300ETF", "fullName": "华泰柏瑞沪深300ETF"}), "华泰柏瑞沪深300ETF")


if __name__ == "__main__":
    unittest.main()

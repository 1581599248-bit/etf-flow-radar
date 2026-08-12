import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import update_daily


class PipelineTests(unittest.TestCase):
    def test_index_mapping_avoids_partial_false_matches(self):
        self.assertEqual(update_daily.identify_index("沪深300ETF") [0], "000300")
        self.assertEqual(update_daily.identify_index("科创50ETF") [0], "000688")
        self.assertIsNone(update_daily.identify_index("港股通50ETF"))

    def test_percentile_requires_enough_history(self):
        self.assertIsNone(update_daily.percentile([1.0] * 59, 1.0))
        self.assertEqual(update_daily.percentile([1.0] * 60, 1.0), 100.0)

    def test_failed_snapshot_never_replaces_latest(self):
        with tempfile.TemporaryDirectory() as temp:
            public = Path(temp)
            (public / "latest.json").write_text('{"safe": true}', "utf-8")
            with patch.object(update_daily, "PUBLIC", public):
                with self.assertRaises(RuntimeError):
                    update_daily.atomic_publish({"status": "failed", "tradeDate": "2026-08-11"})
            self.assertEqual(json.loads((public / "latest.json").read_text("utf-8")), {"safe": True})


if __name__ == "__main__":
    unittest.main()

import copy
import json
from pathlib import Path
import unittest
import tempfile
import supplement_order_flow as recovery
from unittest.mock import patch
import pandas as pd
import eastmoney_etf_spot as transport
from supplement_order_flow import supplement

ROOT = Path(__file__).resolve().parents[1]


class TransportTests(unittest.TestCase):
    def test_failover_restarts_complete_pagination(self):
        def page(host, number):
            if host == transport.HOSTS[0]:
                raise OSError('502')
            rows = [{key: 0 for key in transport.FIELDS} for _ in range(100)]
            for i, row in enumerate(rows):
                row.update(f12=str(510000 + (number-1)*100+i), f297=20260921, f124=1789974000)
            return {'total': 500, 'diff': rows}
        with patch.object(transport, '_page', side_effect=page):
            result = transport.fetch_spot()
        self.assertEqual(len(result), 500)
        self.assertEqual(result['代码'].nunique(), 500)
        self.assertEqual(result.attrs['endpoint'], transport.HOSTS[1])
        self.assertEqual(str(result['数据日期'].iloc[0].date()), '2026-09-21')

    def test_duplicate_page_is_rejected_not_summed(self):
        data = {'total': 500, 'diff': [{'f12': str(510000+i)} for i in range(100)]}
        with patch.object(transport, '_page', return_value=data):
            with self.assertRaises(RuntimeError): transport.fetch_spot()


class SupplementTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = json.loads((ROOT/'site/data/history/2026-09-21.json').read_text())
        self.fact = json.loads((ROOT/'site/data/order_flow/2026-09-18.json').read_text())
        self.fact['tradeDate'] = self.snapshot['tradeDate']

    def test_recovery_preserves_all_primary_values_and_is_idempotent(self):
        original = copy.deepcopy(self.snapshot)
        updated = supplement(original, self.fact)
        self.assertEqual(original, self.snapshot)
        for key in ('market', 'groups', 'universe', 'industryRollups'):
            self.assertEqual(updated[key], original[key])
        self.assertEqual(updated['flowMetrics']['primaryMarket'], original['flowMetrics']['primaryMarket'])
        self.assertEqual(updated['flowMetrics']['secondaryMarketTradeFlow']['status'], 'available')
        self.assertEqual(supplement(updated, self.fact), updated)

    def test_main_audits_with_fact_before_publishing(self):
        snapshot = json.loads((ROOT/'site/data/history/2026-09-18.json').read_text())
        snapshot['flowMetrics']['secondaryMarketTradeFlow'] = {'status': 'unavailable'}
        fact = json.loads((ROOT/'site/data/order_flow/2026-09-18.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            public = Path(directory)
            (public/'daily').mkdir()
            (public/'daily/2026-09-18.json').write_text((ROOT/'site/data/daily/2026-09-18.json').read_text())
            (public/'order_flow').mkdir()
            (public/'order_flow/2026-09-18.json').write_text(json.dumps(fact))
            (public/'latest.json').write_text(json.dumps(snapshot))
            with patch.object(recovery.pipeline.base, 'PUBLIC', public):
                recovery.main()
            updated = json.loads((public/'latest.json').read_text())
            self.assertEqual(updated['flowMetrics']['secondaryMarketTradeFlow']['status'], 'available')
            self.assertEqual(updated['flowMetrics']['primaryMarket'], snapshot['flowMetrics']['primaryMarket'])

    def test_wrong_day_rejected(self):
        self.fact['tradeDate'] = '2026-09-18'
        with self.assertRaises(ValueError): supplement(self.snapshot, self.fact)

    def test_partial_fact_rejected(self):
        self.fact['etfs'] = self.fact['etfs'][:10]
        with self.assertRaises(ValueError): supplement(self.snapshot, self.fact)


if __name__ == '__main__': unittest.main()

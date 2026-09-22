import unittest
from datetime import date
from tencent_etf_spot import parse_quotes


class TencentQuoteTests(unittest.TestCase):
    def quote(self):
        fields = [''] * 38
        for index, value in {1: '测试ETF', 2: '510300', 6: '1000', 7: '600',
                             8: '400', 30: '20260921161459',
                             35: '4.6/1000/460000', 37: '46'}.items():
            fields[index] = value
        return fields

    def parse(self, fields):
        return parse_quotes('v_sh510300="' + '~'.join(fields) + '";', ['sh510300'], date(2026, 9, 21))

    def test_units_direction_and_missing_main_order_are_preserved(self):
        row = self.parse(self.quote())[0]
        self.assertEqual(row['成交额'], 460000)
        self.assertEqual(row['外盘'], 600)
        self.assertEqual(row['内盘'], 400)
        self.assertIsNone(row['主力净流入-净额'])

    def test_wrong_date_or_intraday_rejected(self):
        for stamp in ['20260918161459', '20260922161459', '20260921140000']:
            fields = self.quote(); fields[30] = stamp
            with self.assertRaises(ValueError): self.parse(fields)

    def test_wrong_units_rejected(self):
        fields = self.quote(); fields[37] = '460000'
        with self.assertRaises(ValueError): self.parse(fields)

    def test_missing_symbol_rejected(self):
        with self.assertRaises(ValueError): parse_quotes('', ['sh510300'], date(2026, 9, 21))


if __name__ == '__main__': unittest.main()

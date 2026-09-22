"""Whole-universe Tencent fallback; never relabel stale quotes or mix providers."""
from __future__ import annotations
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as clock
from pathlib import Path
from urllib.request import Request, urlopen
import pandas as pd

PUBLIC = Path(__file__).resolve().parents[1] / 'site/data'


def parse_quotes(text, symbols, day):
    matches = re.findall(r'v_((?:sh|sz)\d{6})="([^"]*)";', text)
    quotes = dict(matches)
    if len(quotes) != len(matches) or set(quotes) != set(symbols):
        raise ValueError('Tencent batch omitted or duplicated requested ETF symbols')
    rows = []
    for symbol in symbols:
        fields = quotes[symbol].split('~')
        if len(fields) < 38 or fields[2] != symbol[2:]:
            raise ValueError(f'Tencent schema/code mismatch: {symbol}')
        stamp = datetime.strptime(fields[30], '%Y%m%d%H%M%S')
        if stamp.date() != day or stamp.time() < clock(15):
            raise ValueError(f'Tencent quote is not requested closing session: {symbol} {stamp}')
        amount = float(fields[35].split('/')[2])
        outer, inner, volume = map(float, (fields[7], fields[8], fields[6]))
        if not all(math.isfinite(x) and x >= 0 for x in (amount, outer, inner, volume)):
            raise ValueError(f'Tencent invalid numeric quote: {symbol}')
        if abs(amount / 10000 - float(fields[37])) > 1.1:
            raise ValueError(f'Tencent turnover units do not reconcile: {symbol}')
        if abs(outer + inner - volume) > max(2, volume * 0.001):
            raise ValueError(f'Tencent directional volume does not reconcile: {symbol}')
        rows.append({'代码': fields[2], '名称': fields[1], '成交额': amount,
                     '外盘': outer, '内盘': inner, '主力净流入-净额': None,
                     '最新份额': None, '数据日期': day.isoformat(),
                     '更新时间': stamp.isoformat() + '+08:00'})
    return rows


def fetch_spot(day):
    snapshot = json.loads((PUBLIC / 'latest.json').read_text('utf-8'))
    symbols = sorted({('sh' if r['exchange'] == 'SSE' else 'sz') + r['code']
                      for r in snapshot['universe'] if r['exchange'] in ('SSE', 'SZSE')})
    if len(symbols) < 500:
        raise ValueError('Official ETF universe is incomplete')
    batches = [symbols[i:i+40] for i in range(0, len(symbols), 40)]
    def batch(items):
        for attempt in range(3):
            try:
                request = Request('https://qt.gtimg.cn/q=' + ','.join(items),
                                  headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://gu.qq.com/'})
                with urlopen(request, timeout=25) as response:
                    text = response.read().decode('gb18030')
                return parse_quotes(text, items, day)
            except Exception:
                if attempt == 2: raise
                time.sleep(2)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = [row for part in pool.map(batch, batches) for row in part]
    frame = pd.DataFrame(rows)
    frame.attrs.update(endpoint='qt.gtimg.cn', providerTotal=len(symbols),
                       source='腾讯财经ETF收盘行情 成交额 + 外盘/内盘')
    print(f'Tencent complete ETF universe: {len(frame)}; exact close date={day}', flush=True)
    return frame

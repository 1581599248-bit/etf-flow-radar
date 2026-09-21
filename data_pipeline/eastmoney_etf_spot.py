"""Same-provider ETF quotes with bounded requests and complete-page failover."""
from __future__ import annotations
import json
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import pandas as pd

HOSTS = ('push2delay.eastmoney.com', 'push2.eastmoney.com', 'push2his.eastmoney.com')
FIELDS = {'f12': '代码', 'f14': '名称', 'f2': '最新价', 'f5': '成交量',
          'f6': '成交额', 'f34': '外盘', 'f35': '内盘', 'f62': '主力净流入-净额',
          'f38': '最新份额', 'f297': '数据日期', 'f124': '更新时间'}


def _page(host, page):
    params = dict(pn=page, pz=100, po=1, np=1, fltt=2, invt=2, fid='f12',
                  fs='b:MK0021,b:MK0022,b:MK0023,b:MK0024,b:MK0827',
                  fields=','.join(FIELDS))
    url = f'https://{host}/api/qt/clist/get?{urlencode(params)}'
    for attempt in range(2):
        try:
            with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=20) as response:
                payload = json.load(response)
            if payload.get('rc') != 0 or not isinstance(payload.get('data'), dict):
                raise ValueError('ETF quote provider returned an invalid payload')
            return payload['data']
        except Exception:
            if attempt: raise
            time.sleep(1)


def fetch_spot():
    errors = []
    for host in HOSTS:
        try:
            rows, seen, expected = [], set(), None
            for page in range(1, 101):
                data = _page(host, page)
                total = int(data.get('total', 0))
                if expected is None: expected = total
                if not 500 <= total <= 10000 or total != expected:
                    raise ValueError('ETF universe total invalid or changed during pagination')
                part = data.get('diff')
                if isinstance(part, dict): part = list(part.values())
                if not isinstance(part, list) or not part:
                    raise ValueError('ETF pagination ended before the advertised total')
                for row in part:
                    code = str(row.get('f12', ''))
                    if len(code) != 6 or not code.isdigit() or code in seen:
                        raise ValueError('ETF pagination contains invalid or duplicate codes')
                    seen.add(code)
                rows.extend(part)
                if len(rows) >= expected: break
            if len(rows) != expected:
                raise ValueError('ETF pagination did not reconcile to the advertised total')
            frame = pd.DataFrame(rows).rename(columns=FIELDS)
            if not set(FIELDS.values()).issubset(frame.columns):
                raise ValueError('ETF quote schema changed')
            for col in set(FIELDS.values()) - {'代码', '名称', '数据日期', '更新时间'}:
                frame[col] = pd.to_numeric(frame[col], errors='coerce')
            frame['数据日期'] = pd.to_datetime(frame['数据日期'].astype(str), format='%Y%m%d', errors='coerce')
            frame['更新时间'] = pd.to_datetime(frame['更新时间'], unit='s', utc=True, errors='coerce').dt.tz_convert('Asia/Shanghai')
            frame.attrs['endpoint'] = host
            frame.attrs['providerTotal'] = expected
            print(f'ETF quotes: endpoint={host}; complete rows={expected}', flush=True)
            return frame
        except Exception as exc:
            errors.append(f'{host}: {exc}')
            print(f'ETF quote endpoint failed: {host}: {exc}', flush=True)
    raise RuntimeError('All Eastmoney ETF endpoints failed: ' + '; '.join(errors))

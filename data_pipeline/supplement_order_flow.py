"""Attach a late exact-date trading fact to an already verified official report.

No shares, NAVs, classifications, primary flows or history calculations are rebuilt.
"""
from __future__ import annotations
import copy
import json
import shutil
from datetime import date, datetime
from pathlib import Path
import tempfile
import pandas as pd
import update_daily_v2 as pipeline
from capture_order_flow_v2 import CN, MIN_ROWS
from audit_snapshot_v6 import audit


def supplement(snapshot, fact):
    if snapshot.get('status') != 'verified' or snapshot.get('sourceMode') != 'REAL':
        raise ValueError('only a verified REAL report may be supplemented')
    if fact.get('tradeDate') != snapshot.get('tradeDate'):
        raise ValueError('trading fact date must match the report exactly')
    if fact.get('metric') != 'secondaryMarketETFTradingFlow' or len(fact.get('etfs', [])) < MIN_ROWS:
        raise ValueError('trading fact missing or incomplete')
    result = copy.deepcopy(snapshot)
    day = date.fromisoformat(result['tradeDate'])
    frame = pd.DataFrame(result['universe'])
    frame['scope'] = frame['assetScope']
    ths = pd.DataFrame({'code': frame['code'], 'fund_name': frame['name'], 'fund_type': frame['fundType']})
    spot = pipeline._order_flow_payload_to_frame(fact, day)
    secondary, per_etf = pipeline.flow_model_v2._secondary_order_flow(frame, spot, day)
    result['flowMetrics']['secondaryMarketOrderFlow'] = secondary
    result['quality']['secondaryOrderFlowStatus'] = secondary['status']
    for item in result['etfs']:
        value = per_etf.get(item['code'])
        item['secondaryMainOrderFlow1d'] = round(value, 2) if value is not None else None
    pipeline._add_trade_net_flow(result, day, ths, spot)
    pipeline._regenerate_v2_conclusion(result)
    # Idempotent on repeated capture schedules.
    if result == snapshot:
        return result
    result['generatedAt'] = datetime.now(CN).isoformat(timespec='seconds')
    return result


def main():
    path = pipeline.base.PUBLIC / 'latest.json'
    if not path.exists(): return
    snapshot = json.loads(path.read_text('utf-8'))
    fact_path = pipeline.base.PUBLIC / 'order_flow' / f"{snapshot['tradeDate']}.json"
    if not fact_path.exists():
        print('No exact-date fact for the published report; official publication remains independent')
        return
    fact = json.loads(fact_path.read_text('utf-8'))
    if fact.get('metric') != 'secondaryMarketETFTradingFlow': return
    result = supplement(snapshot, fact)
    if result == snapshot:
        print('Published report already contains this trading fact')
        return
    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory) / 'candidate.json'
        candidate.write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
        evidence = Path(directory) / 'order_flow' / fact_path.name
        evidence.parent.mkdir()
        evidence.write_text(json.dumps(fact, ensure_ascii=False), 'utf-8')
        daily = Path(directory) / 'daily'
        daily.mkdir()
        shutil.copy2(pipeline.base.PUBLIC / 'daily' / fact_path.name, daily / fact_path.name)
        checks = audit(candidate)
    pipeline.base.atomic_publish(result)
    print(f"Supplemented {result['tradeDate']}; audit checks={len(checks)}; "
          f"trade={result['flowMetrics']['secondaryMarketTradeFlow']['scopeTotals']['aShareStockEtf']}")


if __name__ == '__main__':
    main()

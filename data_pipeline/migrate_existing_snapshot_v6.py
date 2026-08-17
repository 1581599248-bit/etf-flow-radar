"""One-time / historical schema-v6 migration from already archived facts.

This is intentionally offline with respect to SSE/SZSE share history.  A JSON
methodology migration should not fail merely because a historical exchange HTTP
endpoint blocks a GitHub runner.  It uses:

* the immutable archived T and T-1 exchange universes already in the repository;
* exact-date THS NAV/fund type;
* the previous verified snapshot's 5/20-day endpoint amounts only to reconstruct
  the corresponding historical share endpoints, then revalues those endpoints
  with canonical NAV.

No synthetic headline target is introduced.  If the required archived facts are
missing, migration fails rather than guessing.
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

import update_daily as base
import update_daily_production as production
import update_daily_v2 as v2

CN = ZoneInfo("Asia/Shanghai")
FACTORS = (0.2, 0.25, 1 / 3, 0.5, 2.0, 3.0, 4.0, 5.0)


def _archived_universe(day: date) -> pd.DataFrame:
    path = base.PUBLIC / "universe" / f"{day.isoformat()}.json"
    if not path.exists():
        raise FileNotFoundError(f"missing archived exchange universe: {path}")
    payload = json.loads(path.read_text("utf-8"))
    frame = pd.DataFrame(payload.get("universe", []))
    required = {"code", "name", "shares", "exchange"}
    if frame.empty or not required.issubset(frame.columns):
        raise ValueError(f"invalid archived universe for {day}")
    frame["code"] = frame["code"].astype(str).str.zfill(6)
    frame["shares"] = pd.to_numeric(frame["shares"], errors="coerce")
    return frame.dropna(subset=["shares"]).drop_duplicates("code", keep="last")


def _factor(prev_shares: float, cur_shares: float, prev_nav: float, nav: float) -> float:
    values = (prev_shares, cur_shares, prev_nav, nav)
    if not all(math.isfinite(float(x)) and float(x) > 0 for x in values):
        return 1.0
    ratio = cur_shares / prev_shares
    factor = min(FACTORS, key=lambda x: abs(ratio / x - 1))
    if abs(ratio / factor - 1) > 0.05:
        return 1.0
    if abs((nav / prev_nav) / (1 / factor) - 1) > 0.12:
        return 1.0
    return float(factor)


def _old_endpoint_shares(
    current_shares: float,
    old_record: dict | None,
    flow_field: str,
) -> float:
    if not old_record:
        return math.nan
    flow = old_record.get(flow_field)
    price = old_record.get("referencePrice")
    if not isinstance(flow, (int, float)) or not isinstance(price, (int, float)) or price <= 0:
        return math.nan
    # Legacy endpoint flow = (T shares - endpoint shares) * legacy reference price / 1e8.
    return float(current_shares) - float(flow) * 1e8 / float(price)


def _synthetic_window(
    snapshot: dict,
    day: date,
    current: pd.DataFrame,
    previous: pd.DataFrame,
    ths: pd.DataFrame,
) -> list[tuple[date, pd.DataFrame]]:
    old = {str(row.get("code")): row for row in snapshot.get("etfs", [])}
    nav_map = ths.set_index("code")[["nav", "prev_nav"]].to_dict("index")
    previous_map = previous.set_index("code")["shares"].to_dict()

    rows: list[dict] = []
    for row in current[["code", "shares"]].itertuples(index=False):
        code = str(row.code)
        cur = float(row.shares)
        prv_raw = previous_map.get(code, math.nan)
        nav_row = nav_map.get(code, {})
        nav = nav_row.get("nav", math.nan)
        prev_nav = nav_row.get("prev_nav", math.nan)
        factor = _factor(float(prv_raw), cur, float(prev_nav), float(nav)) if pd.notna(prv_raw) else 1.0
        prv = float(prv_raw) * factor if pd.notna(prv_raw) else math.nan
        record = old.get(code)
        rows.append({
            "code": code,
            "current": cur,
            "previous": prv,
            "five": _old_endpoint_shares(cur, record, "flow5d"),
            "twenty": _old_endpoint_shares(cur, record, "flow20d"),
        })
    matrix = pd.DataFrame(rows)

    # flow_model_v2 only uses positions -21, -6, -2 and -1. Other sessions are
    # deliberately NaN because this migration does not invent unseen historical shares.
    window: list[tuple[date, pd.DataFrame]] = []
    for index in range(21):
        if index == 0:
            values = matrix[["code", "twenty"]].rename(columns={"twenty": "shares"})
        elif index == 15:
            values = matrix[["code", "five"]].rename(columns={"five": "shares"})
        elif index == 19:
            values = matrix[["code", "previous"]].rename(columns={"previous": "shares"})
        elif index == 20:
            values = matrix[["code", "current"]].rename(columns={"current": "shares"})
        else:
            values = pd.DataFrame({"code": matrix["code"], "shares": math.nan})
        window.append((day - timedelta(days=20 - index), values))
    return window


def migrate(day: date) -> dict:
    latest_path = base.PUBLIC / "latest.json"
    snapshot = json.loads(latest_path.read_text("utf-8"))
    if snapshot.get("tradeDate") != day.isoformat():
        raise ValueError(f"latest snapshot is {snapshot.get('tradeDate')}, not {day}")
    if int(snapshot.get("schemaVersion", 0)) >= 6:
        print(f"snapshot already schema v{snapshot['schemaVersion']}; no migration needed")
        return snapshot

    previous_day = date.fromisoformat(snapshot["previousTradeDate"])
    current = _archived_universe(day)
    previous = _archived_universe(previous_day)
    ths = production._get_ths_day(day)

    current_map = current.set_index("code")["shares"].to_dict()
    for record in snapshot.get("universe", []):
        code = str(record.get("code"))
        if code in current_map:
            record["shares"] = float(current_map[code])

    window = _synthetic_window(snapshot, day, current, previous, ths)
    v2.apply_v2_semantics(snapshot, day, window, ths, v2._load_secondary_spot(day))
    snapshot["generatedAt"] = datetime.now(CN).isoformat(timespec="seconds")
    snapshot.setdefault("quality", {})["schemaMigration"] = {
        "version": 6,
        "source": "archived_exchange_T_Tminus1_plus_verified_legacy_endpoints",
        "historicalNetworkFetch": False,
        "note": "1日份额完全由归档交易所T/T-1重算；5/20日仅对已有已验证端点份额信息换算为NAV口径，不填造缺失历史。",
    }
    return snapshot


def publish(snapshot: dict) -> None:
    text = json.dumps(snapshot, ensure_ascii=False, indent=2)
    (base.PUBLIC / "latest.json").write_text(text, "utf-8")
    history = base.PUBLIC / "history"
    history.mkdir(parents=True, exist_ok=True)
    (history / f'{snapshot["tradeDate"]}.json').write_text(text, "utf-8")
    daily = base.PUBLIC / "daily"
    daily.mkdir(parents=True, exist_ok=True)
    daily_text = json.dumps(v2.daily_flow_payload(snapshot), ensure_ascii=False, indent=2)
    (daily / f'{snapshot["tradeDate"]}.json').write_text(daily_text, "utf-8")
    (daily / "latest.json").write_text(daily_text, "utf-8")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--date", required=True, help="archived trade date YYYY-MM-DD")
    args = parser.parse_args()
    snapshot = migrate(date.fromisoformat(args.date))
    if int(snapshot.get("schemaVersion", 0)) >= 6:
        publish(snapshot)
    print(
        f"schema v{snapshot.get('schemaVersion')} {snapshot.get('tradeDate')}: "
        f"{snapshot.get('market', {}).get('etfCount')} ETFs, "
        f"primary={snapshot.get('market', {}).get('flow1d')}亿"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

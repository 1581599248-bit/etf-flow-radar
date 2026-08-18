"""Production entrypoint with the unified ETF system contract.

The validated schema-v6 collectors and corporate-action logic remain the base
engine.  This wrapper adds system_contract_v7 as the final, mandatory semantic
and reconciliation layer before anything is written to site/data.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

import pandas as pd

import system_contract_v7 as contract
import update_daily as base
import update_daily_v2 as v2

_ORIG_APPLY = v2.apply_v2_semantics
_ORIG_DAILY_PAYLOAD = v2.daily_flow_payload
_ORIG_LOAD_SECONDARY_SPOT = v2._load_secondary_spot


def _load_secondary_spot(day: date) -> pd.DataFrame:
    """Read the precise v3 secondary-trading file; retain old files as fallback."""
    path = base.PUBLIC / "order_flow" / f"{day.isoformat()}.json"
    if path.exists():
        payload = json.loads(path.read_text("utf-8"))
        if payload.get("tradeDate") == day.isoformat() and payload.get("metric") == "secondaryMarketAggressorImbalanceEstimate":
            rows = payload.get("etfs", [])
            if rows:
                frame = pd.DataFrame(rows)
                frame["代码"] = frame["code"].astype(str).str.zfill(6)
                frame["名称"] = frame["name"].astype(str)
                frame["数据日期"] = day.isoformat()
                frame["主力净流入-净额"] = pd.to_numeric(frame["vendorMainOrderNet1d"], errors="coerce") * 1e8
                frame["当日交易净额"] = pd.to_numeric(frame["aggressorImbalance1d"], errors="coerce") * 1e8
                frame["当日交易流入"] = pd.to_numeric(frame["buyInitiatedEstimate1d"], errors="coerce") * 1e8
                frame["当日交易流出"] = pd.to_numeric(frame["sellInitiatedEstimate1d"], errors="coerce") * 1e8
                frame["成交额"] = pd.to_numeric(frame["amount"], errors="coerce") * 1e8
                return frame[[
                    "代码", "名称", "主力净流入-净额", "当日交易净额",
                    "当日交易流入", "当日交易流出", "成交额", "数据日期",
                ]]
    return _ORIG_LOAD_SECONDARY_SPOT(day)


def _apply_contract(
    snapshot: dict[str, Any],
    day: date,
    share_window: list[tuple[date, pd.DataFrame]],
    ths: pd.DataFrame,
    spot: pd.DataFrame | None,
) -> None:
    _ORIG_APPLY(snapshot, day, share_window, ths, spot)
    contract.apply_system_contract(snapshot, day, share_window)


def _daily_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    payload = _ORIG_DAILY_PAYLOAD(snapshot)
    payload["dataContractVersion"] = contract.CONTRACT_VERSION
    payload["directionToleranceShares"] = contract.DIRECTION_EPS_SHARES
    payload["methodology"] = {
        "flow": snapshot.get("methodology", {}).get("flow"),
        "classification": snapshot.get("methodology", {}).get("classification"),
        "multiDay": snapshot.get("methodology", {}).get("multiDay"),
    }
    return payload


def install_pipeline() -> None:
    v2._load_secondary_spot = _load_secondary_spot
    v2.apply_v2_semantics = _apply_contract
    v2.daily_flow_payload = _daily_payload
    v2.install_v2_pipeline()


def main() -> int:
    install_pipeline()
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())

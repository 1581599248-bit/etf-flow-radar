"""Compatibility entrypoint for the secondary-market capture task.

The old v2 implementation used cash-flow wording for an outer/inner-volume
aggressor imbalance.  All callers now use the precise v3 implementation.
"""
from capture_order_flow_v3 import (  # noqa: F401
    CN,
    MIN_ROWS,
    METRIC,
    OUT,
    ROOT,
    _is_exchange_session,
    build_snapshot,
    main,
    publish,
)

if __name__ == "__main__":
    raise SystemExit(main())

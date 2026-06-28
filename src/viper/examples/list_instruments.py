#!/usr/bin/env python3
"""
List tradeable instruments, grouped by kind, top N per kind by 24h volume.

"What can I trade?" — the instruments endpoint returns the full catalog
(hundreds of markets). Dumping all of them is noise; this ranks each category
(perp / spot / hip3) by 24h volume and shows the most active few, with every
field the endpoint returns. So you get both the lay of the land and the
complete row shape your bot will parse.

Read-only: no account state, no orders. Needs only API credentials.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle      # optional
    viper-examples list-instruments

Tuning (optional env vars):
    VIPER_EXAMPLE_TOP=5        # instruments per kind (default 3)
    VIPER_EXAMPLE_COMPACT=1    # one line each, skip the full field dump
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperRestClient, ViperError

ORDER = 13
SECTION = "Account & Market Data"
DESCRIPTION = "List tradeable instruments, grouped by kind, top N by 24h volume."

KINDS = ("perp", "spot", "hip3")


def _vol(row: dict) -> float:
    """Sort key: 24h USD volume, with missing/None sorted to the bottom."""
    v = row.get("volume_24h")
    return v if isinstance(v, (int, float)) else -1.0


def _headline(row: dict) -> str:
    vol = row.get("volume_24h")
    vol_s = f"${vol:,.0f}" if isinstance(vol, (int, float)) else "n/a"
    return (f"  {row['symbol']:<18} vol_24h={vol_s:>18}  "
            f"min_order_value=${row.get('min_order_value')}  "
            f"max_lev={row.get('max_leverage')}  "
            f"settle={row.get('settlement_currency')}")


async def main() -> None:
    top = int(os.environ.get("VIPER_EXAMPLE_TOP", "3"))
    compact = bool(os.environ.get("VIPER_EXAMPLE_COMPACT"))

    rest = ViperRestClient.from_env()
    async with rest:
        resp = await rest.instruments()          # envelope: {items, count, status}
    items = resp.get("items", [])

    by_kind: dict[str, list] = {}
    for row in items:
        by_kind.setdefault(row.get("kind", "other"), []).append(row)

    print(f"{len(items)} instruments total — "
          + ", ".join(f"{k}={len(by_kind.get(k, []))}" for k in KINDS))

    for kind in KINDS:
        rows = sorted(by_kind.get(kind, []), key=_vol, reverse=True)[:top]
        print(f"\n=== {kind} — top {len(rows)} by 24h volume ===")
        if not rows:
            print("  (none)")
            continue
        for row in rows:
            print(_headline(row))
            if not compact:
                # Every field the endpoint returns for this instrument.
                for key in sorted(row):
                    print(f"      {key}: {row[key]}")
            print()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ViperError as e:
        raise SystemExit(f"API error: {e}")

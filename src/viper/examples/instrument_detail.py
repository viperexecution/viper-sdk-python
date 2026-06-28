#!/usr/bin/env python3
"""
Full detail for one instrument via GET /v1/instruments/{symbol}.

"What does one instrument actually look like?" — fetches the complete metadata
object for a single symbol and prints every field, so you see the exact shape
your bot will parse: sz_decimals, min_order_value, settlement_currency, the
HIP-3 fields (dex_name, margin_mode), prices, funding, and so on. The endpoint
also returns a sample validation result (size 1.0 at mark) — handy for seeing
what order validation will report.

Read-only. Needs only API credentials.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_EXAMPLE_SYMBOL=BTC       # which instrument (default BTC)
    viper-examples instrument-detail

    # HIP-3 needs the namespace prefix:
    export VIPER_EXAMPLE_SYMBOL=xyz:SILVER
    viper-examples instrument-detail
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperRestClient, ViperError

ORDER = 14
SECTION = "Account & Market Data"
DESCRIPTION = "Full metadata for one instrument (GET /v1/instruments/{symbol})."


async def main() -> None:
    symbol = os.environ.get("VIPER_EXAMPLE_SYMBOL", "BTC")

    rest = ViperRestClient.from_env()
    async with rest:
        # Dedicated single-symbol endpoint, tolerant of variant casing/format.
        # For exact-match-only lookup use rest.instruments(symbol=...).
        resp = await rest.instrument(symbol)

    # Response shape: {instrument: {...full row...}, validation_example: {...} | null}
    row = (resp or {}).get("instrument")
    if not isinstance(row, dict) or not row.get("symbol"):
        print(f"No instrument detail returned for '{symbol}'.")
        print("HIP-3 symbols need the namespace prefix, e.g. set "
              "VIPER_EXAMPLE_SYMBOL=xyz:SILVER")
        return

    print(f"{row['symbol']}  (kind={row.get('kind')}, api_symbol={row.get('api_symbol')})")
    for key in sorted(row):
        print(f"  {key}: {row[key]}")

    val = resp.get("validation_example")
    if val:
        print("\nvalidation_example (size 1.0 at mark):")
        for key in sorted(val):
            print(f"  {key}: {val[key]}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ViperError as e:
        raise SystemExit(f"API error: {e}")

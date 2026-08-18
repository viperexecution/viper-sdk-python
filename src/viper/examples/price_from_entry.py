#!/usr/bin/env python3
"""
Indicator-priced entry — rest a limit AT a level, not at a number.

The same resolve-once machinery that prices derived TP/SL legs also
prices ENTRIES: `price_from` on POST /v1/order resolves an indicator
level at submission and rests the limit there. "Buy the Bollinger
lower" as one call — no separate indicator read, no arithmetic, no
staleness between reading a level and acting on it.

The response echoes `price_from_resolution` (indicator value, bar time,
resolved price) so the fill logic is auditable — the same echo shape
derived TP/SL legs return.

Composes with everything an ordinary limit does: post_only, TP/SL legs
(absolute or derived — see `tpsl-order-legs`). This rests one entry at
the lower band with an ATR-offset stop attached, shows both echoes, and
cancels.

Fires a REAL resting order on mainnet (~$15 notional); tidies up.

Run (after `pip install viper-execution`):
    viper-examples price-from-entry

Tuning (optional env vars):
    VIPER_EXAMPLE_SYMBOL=BTC
    VIPER_EXAMPLE_USD=15
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperRestClient, ViperError


ORDER = 16
SECTION = "TP/SL & Exits"
DESCRIPTION = "Rest an entry at an indicator level (price_from) — with a derived stop attached."


async def main() -> None:
    symbol = os.environ.get("VIPER_EXAMPLE_SYMBOL", "BTC")
    usd = float(os.environ.get("VIPER_EXAMPLE_USD", "15"))

    rest = ViperRestClient.from_env()
    async with rest:
        inst = (await rest.instrument(symbol)).get("instrument") or {}
        sz_decimals = inst.get("sz_decimals")
        price = await rest.price(symbol)
        mark = price.get("ask") or price.get("mark_price") or price.get("price")
        if not isinstance(sz_decimals, int) or not isinstance(mark, (int, float)):
            raise SystemExit(f"Could not size {symbol}: {inst!r} / {price!r}")
        q = 10 ** -sz_decimals
        size = max(q, ((usd / mark) // q) * q)
        print(f"{symbol} @ ${mark:,.2f} — resting a buy at the Bollinger "
              f"lower (1h)\n")

        r = await rest.place_order(
            symbol=symbol, side="buy", size=size, order_type="limit",
            post_only=True,
            price_from={"indicator": "bollinger", "series": "lower",
                        "interval": "1h"},
            stop_loss={"offset": {"indicator": "atr", "mult": 2.0,
                                  "interval": "1h"}})

        res = r.get("price_from_resolution") or {}
        print(f"entry:  status={r.get('status')}  order_ids={r.get('order_ids')}")
        print(f"        resolved price = {res.get('resolved_price')}  "
              f"[{res.get('indicator')} @ bar {res.get('bar_time')}]")
        sl = r.get("sl_order") or {}
        print(f"sl leg: queued={sl.get('queued')} — resolves against the "
              f"REAL fill if/when the entry fills")

        await rest.cancel_all(symbol=symbol)
        print("\ntidied: entry cancelled (the queued leg retires with it).")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ViperError as e:
        raise SystemExit(f"API error: {e}")

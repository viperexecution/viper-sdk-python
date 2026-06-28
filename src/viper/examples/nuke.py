#!/usr/bin/env python3
"""
Emergency flatten — cancel orders and close positions in one call.

nuke is the panic button: POST /v1/nuke cancels open orders, then closes positions,
for the resolved wallet. The SDK requires confirm=True — a deliberate "yes I mean it"
step, since this is destructive.

    rest.nuke(confirm=True)                       # flatten everything (this wallet)
    rest.nuke(confirm=True, side="long")          # long positions + buy orders only
    rest.nuke(confirm=True, side="short")         # short positions + sell orders only
    rest.nuke(confirm=True, symbol="BTC")         # limit the blast radius to one symbol
    rest.nuke(confirm=True, side="long", symbol="BTC")   # longs on BTC only

The `side` filter applies to BOTH legs consistently: "long" cancels buy orders and
closes long positions; "short" cancels sell orders and closes short positions; "all"
(default) flattens both. `symbol` and `side` compose — they intersect.

To show side-scoping, this builds a book with BOTH directions (long BTC + long ETH,
short SP500), then nukes side="long" (watch the longs flatten while the short
survives), then nukes side="all" to clean up.

This FILLS small market orders (~$15 each) to create positions, then closes them via
the nukes. Run knowing it takes brief real positions across a few symbols.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle
    export VIPER_WALLET=0x...
    viper-examples nuke

Tuning (optional env vars):
    VIPER_EXAMPLE_USD=15          # size of each demo position/order (default 15)
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperRestClient, ViperError


ORDER = 17
KIND = "rest"
SECTION = "Account & Market Data"
DESCRIPTION = "Emergency flatten: cancel orders + close positions, optionally by side (POST /v1/nuke)."

# Demo book: (symbol, position_side). Long BTC + long ETH + short SP500 (HIP-3) gives
# us both directions so the side filter has something to discriminate.
_BOOK = [
    ("BTC", "buy"),         # long BTC
    ("ETH", "buy"),         # long ETH
    ("xyz:SP500", "sell"),  # short SP500 (HIP-3)
]


async def _mark(rest, symbol):
    px = await rest.price(symbol)
    return px.get("ask") or px.get("mark") or px.get("mid") or px.get("price")


async def _snapshot(rest):
    """Return positions and orders as lists of (symbol, side)."""
    pos = await rest.positions()
    od = await rest.orders()
    plist = pos.get("positions") or pos.get("items") or []
    olist = od.get("items") or od.get("orders") or []
    return ([(p.get("symbol"), p.get("side")) for p in plist],
            [(o.get("symbol"), o.get("side")) for o in olist])


async def _nuke(rest, **kwargs):
    """nuke with a one-shot retry if rate-limited."""
    for attempt in (1, 2):
        try:
            return await rest.nuke(confirm=True, scope="wallet", **kwargs)
        except ViperError as e:
            if "rate limit" in str(e).lower() and attempt == 1:
                print("   rate-limited — waiting 11s...")
                await asyncio.sleep(11)
                continue
            raise


def _report(r):
    o = (r or {}).get("orders") or {}
    p = (r or {}).get("positions") or {}
    print(f"   cancelled {o.get('cancelled_count')} order(s), "
          f"closed {p.get('closed_count')} position(s), "
          f"errors={(o.get('error_count') or 0) + (p.get('error_count') or 0)}")


async def main() -> None:
    usd = float(os.environ.get("VIPER_EXAMPLE_USD", "15"))

    try:
        async with ViperRestClient.from_env() as rest:
            # 1) Build a book with both directions.
            print("1) BUILD a book (long BTC, long ETH, short SP500 + OOTM orders)")
            for symbol, pos_side in _BOOK:
                mark = await _mark(rest, symbol)
                if not isinstance(mark, (int, float)) or mark <= 0:
                    print(f"   {symbol}: no price, skipping")
                    continue
                size = max(round(usd / mark, 5), 0.00001)
                try:
                    await rest.place_order(symbol=symbol, side=pos_side, size=size,
                                           order_type="market")
                except ViperError as e:
                    print(f"   {symbol}: position failed ({e})")
                for oside, mult in (("buy", 0.90), ("sell", 1.10)):
                    try:
                        await rest.place_order(symbol=symbol, side=oside, size=size,
                                               order_type="limit",
                                               price=round(mark * mult, 6),
                                               post_only=True,
                                               reduce_only=(oside != pos_side))
                    except ViperError:
                        pass
            await asyncio.sleep(2)

            P, O = await _snapshot(rest)
            print(f"   positions={P}")
            print(f"   orders={O}")

            # 2) NUKE LONG — closes long positions + cancels buy orders; the short
            #    SP500 position and the sell orders should SURVIVE.
            print("\n2) NUKE side=long")
            _report(await _nuke(rest, side="long"))
            await asyncio.sleep(2)
            P, O = await _snapshot(rest)
            print(f"   remaining positions={P}  (expect short SP500 only)")
            print(f"   remaining orders={O}  (expect sells only)")

            # 3) NUKE ALL — flatten whatever's left (the cleanup).
            print("\n3) NUKE side=all (cleanup)")
            _report(await _nuke(rest, side="all"))
            await asyncio.sleep(2)
            P, O = await _snapshot(rest)
            flat = not P and not O
            print(f"   positions={P}  orders={O}  {'(flat)' if flat else '(check UI)'}")

    except ViperError as e:
        raise SystemExit(f"API error: {e}")


if __name__ == "__main__":
    asyncio.run(main())

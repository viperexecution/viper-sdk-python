#!/usr/bin/env python3
"""
Trailing TP/SL on an open position — exit-engine maintained, venue-coupled.

Where `offset` and `at` resolve ONCE, a `trail` leg is re-resolved by the
exit engine on every closed bar of its interval, and the resting trigger
is moved in place (same client order id through every move). Three forms:

  distance trail — mult x indicator, ratchets in the protective
                   direction only. A stop that follows price up but
                   never back down. (`atr` only for distance trails.)
  level trail    — the trigger IS the indicator's value, re-read each
                   bar. A TP that rides a Bollinger band follows it BOTH
                   ways — levels move, so the leg moves with them.
  pct trail      — a plain percentage from the extreme; no indicator:
                   {"trail": {"pct": 1.5}}

This opens a small position, attaches a distance-trail SL and a
level-trail TP via `POST /v1/positions/{symbol}/tpsl` (both venue-coupled:
they track position size and auto-cancel on close), shows the resolution
echo, waits out one closed bar to catch a move, and tidies up.

Trail moves stream as `trailed` frames on the tpsl.watch channel — pair
with `stream-tpsl-watch` in another terminal to watch the ratchet live.
A quiet bar moves nothing; that is the throttle (`min_move_bps`), not a
fault.

Fires a REAL ~$15 position on mainnet; tidies up fully.

Run (after `pip install viper-execution`):
    viper-examples tpsl-trailing

Tuning (optional env vars):
    VIPER_EXAMPLE_SYMBOL=BTC
    VIPER_EXAMPLE_USD=15
    VIPER_EXAMPLE_WAIT=75        # seconds to wait for a closed 1m bar
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperRestClient, ViperError


ORDER = 15
SECTION = "TP/SL & Exits"
DESCRIPTION = "Trailing TP/SL on a position (distance + level trails) — exit-engine maintained."


def _items(r):
    if isinstance(r, dict):
        return r.get("items") or r.get("orders") or r.get("positions") or []
    return r or []


def _leg(r: dict | None) -> str:
    if not r:
        return "n/a"
    res = r.get("resolution") or {}
    out = f"oid={r.get('order_id')}  trigger={r.get('trigger_price')}"
    if res:
        out += (f"  [{res.get('indicator')}={res.get('indicator_value')} "
                f"-> {res.get('resolved_price')}]")
    return out


async def _triggers(rest, symbol):
    return {int(o["order_id"]): o.get("trigger_price")
            for o in _items(await rest.orders())
            if o.get("symbol") == symbol and o.get("is_trigger")}


async def main() -> None:
    symbol = os.environ.get("VIPER_EXAMPLE_SYMBOL", "BTC")
    usd = float(os.environ.get("VIPER_EXAMPLE_USD", "15"))
    wait_s = float(os.environ.get("VIPER_EXAMPLE_WAIT", "75"))

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

        # A position to protect.
        entry = await rest.place_order(symbol=symbol, side="buy", size=size,
                                       order_type="market")
        print(f"opened {entry.get('filled_size')} {symbol} @ "
              f"{entry.get('avg_price')}\n")

        # Distance-trail SL (2 x ATR, 1m bars so a move is observable in
        # an example-length run) + level-trail TP riding the upper band.
        r = await rest.set_position_tpsl(
            symbol=symbol,
            stop_loss={"trail": {"indicator": "atr", "mult": 2.0,
                                 "interval": "1m"}},
            take_profit={"trail": {"indicator": "bollinger", "series": "upper",
                                   "interval": "1m"}})
        print("attached (venue-coupled — sized to the position, "
              "auto-cancel on close):")
        print(f"   sl (distance trail): {_leg(r.get('sl_order'))}")
        print(f"   tp (level trail):    {_leg(r.get('tp_order'))}")

        before = await _triggers(rest, symbol)
        print(f"\nwaiting {wait_s:g}s (>= one closed 1m bar) for the exit "
              f"engine to re-resolve…")
        await asyncio.sleep(wait_s)
        after = await _triggers(rest, symbol)

        moved = {o: (before.get(o), t) for o, t in after.items()
                 if o in before and before.get(o) != t}
        if moved:
            for oid, (old, new) in moved.items():
                print(f"   trailed: oid={oid}  {old} -> {new}")
        else:
            print("   triggers unchanged this bar — a quiet bar moves "
                  "nothing (min_move_bps throttle). Moves stream as "
                  "`trailed` frames on tpsl.watch.")

        await rest.cancel_all(symbol=symbol)
        await rest.close_position(symbol=symbol)
        print("\ntidied: trails cancelled, position closed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ViperError as e:
        raise SystemExit(f"API error: {e}")

#!/usr/bin/env python3
"""
Attach TP/SL legs to an algo — armed at terminal state, sized to the fill.

An algo has no fill to anchor to when it launches, so its legs cannot
resolve at submission. Instead they register with a durable watcher and
arm when the execution reaches a TERMINAL STATE WITH FILLS — a completed
run, or one you stop early with a partial position — anchored to the
algo's average fill price and sized to the ACCUMULATED fill.

That last part is the guarantee this example demonstrates: launch a
Glidemaker with a stop-loss leg, let it partially fill, then STOP it.
The stop that appears at the venue covers exactly what filled — not the
requested total — so stopping an algo early never leaves the partial
position unprotected, and never over-hedges it either.

Sequence:
  1. Launch Glidemaker (aggressive) with `stop_loss` (ATR offset) —
     the response reports the leg `queued: true`.
  2. Poll until the execution has partial fills.
  3. Cancel the execution -> the watcher arms the SL, sized to the fill.
  4. Show the venue trigger; tidy up (cancel trigger, close position).

Fires a REAL ~$250 Glidemaker on mainnet; fills partially; tidies up.

Run (after `pip install viper-execution`):
    viper-examples tpsl-algo-legs

Tuning (optional env vars):
    VIPER_EXAMPLE_SYMBOL=BTC
    VIPER_EXAMPLE_USD=250
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperRestClient, ViperError


ORDER = 14
SECTION = "TP/SL & Exits"
DESCRIPTION = "Attach a stop-loss to an algo — arms at terminal state, sized to the accumulated fill."


def _items(r):
    if isinstance(r, dict):
        return r.get("items") or r.get("orders") or r.get("positions") or []
    return r or []


async def main() -> None:
    symbol = os.environ.get("VIPER_EXAMPLE_SYMBOL", "BTC")
    usd = float(os.environ.get("VIPER_EXAMPLE_USD", "250"))

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

        # 1) Launch with a stop-loss leg. It cannot resolve yet — queued.
        r = await rest.execute(
            algo="glidemaker", symbol=symbol, side="buy", total_size=size,
            params={"strategy": "aggressive"},
            stop_loss={"offset": {"indicator": "atr", "mult": 2.0,
                                  "interval": "1h"}})
        eid = r.get("execution_id")
        print(f"launched {eid}  requested={size:g} {symbol}")
        print(f"sl leg: queued={((r.get('sl_order') or {}).get('queued'))} "
              f"(arms at terminal state with fills)\n")

        # 2) Wait for the FIRST partial fills, then stop immediately —
        #    filled < requested is what makes the sizing visible.
        filled = 0.0
        for _ in range(36):                      # up to ~3 minutes
            await asyncio.sleep(5)
            ex = await rest.execution(eid)
            st = (ex.get("state") or {}) if isinstance(ex.get("state"), dict) else ex
            filled = float(st.get("filled_size") or ex.get("filled_size") or 0)
            status = st.get("status") or ex.get("status")
            print(f"  status={status}  filled={filled:g}/{size:g}")
            if filled > 0 or status not in ("running", "pending"):
                break

        if filled <= 0:
            print("\nno fills this run — nothing to protect, so the watch "
                  "will retire instead of arming. Cancelling and exiting.")
            await rest.cancel_execution(eid)
            return

        # 3) Stop the algo mid-fill. Terminal-with-fills -> the leg arms.
        ack = await rest.cancel_execution(eid)
        print(f"\nstopped: status={ack.get('status')} — watcher arms the SL "
              f"sized to the ACCUMULATED fill ({filled:g}), anchored to the "
              f"average fill price")

        # 4) The venue trigger appears within a few seconds.
        trigger = None
        for _ in range(20):
            await asyncio.sleep(3)
            trigs = [o for o in _items(await rest.orders())
                     if o.get("symbol") == symbol and o.get("is_trigger")]
            if trigs:
                trigger = trigs[0]
                break
        if trigger:
            print(f"armed:  oid={trigger.get('order_id')}  "
                  f"trigger={trigger.get('trigger_price')}  "
                  f"size={trigger.get('size')}  (== accumulated fill)  "
                  f"reduce_only={trigger.get('reduce_only')}")
        else:
            print("trigger not visible yet — check stream-tpsl-watch / orders")

        # Tidy: cancel the trigger, close the partial position.
        await rest.cancel_all(symbol=symbol)
        await rest.close_position(symbol=symbol)
        print("tidied: trigger cancelled, position closed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ViperError as e:
        raise SystemExit(f"API error: {e}")

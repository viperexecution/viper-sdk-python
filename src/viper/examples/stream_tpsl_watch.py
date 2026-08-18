#!/usr/bin/env python3
"""
Stream the TP/SL watch lifecycle (tpsl.watch) — from queued to placed, live.

Every `queued: true` in the REST examples points here: when a leg cannot
resolve immediately (resting entry, running algo), a durable watch is
registered, and this channel streams what happens to it.

What you'll see on the stream:
    watch_snapshot   hydration: the wallet's ACTIVE watches (on subscribe)
    queued           a watch registered (source: order_attach / execute)
    placed           the watch fired — protective triggers are live:
                     `attached_oids` + per-leg `resolution` echoes.
                     May be PARTIAL (`partial_errors`), and may be emitted
                     AGAIN for the same watch if the platform re-sizes the
                     protection to the execution's final settled total —
                     the LATEST frame's attached_oids supersede earlier ones.
    cancelled        the watch ended without placing (entry gone / nothing
                     filled at terminal)
    failed           placement attempted and refused — the position may be
                     UNPROTECTED; treat as an action signal
    leg_failed       one leg refused (stage: resolution = permanent;
                     stage: maintain = retrying) — its sibling is unaffected
    leg_recovered    a maintain-stage failure cleared
    trailed          the exit engine moved a trailing trigger on a closed
                     bar (old -> new)

To have something to watch, this launches a Glidemaker with an ATR stop
(REST), streams `queued`, waits for partial fills, stops the algo — and
streams the `placed` frame as the stop arms, sized to the accumulated
fill. Then tidies up.

Fires a REAL ~$250 Glidemaker on mainnet; fills partially; tidies up.

Run ONE instance per wallet+symbol at a time: the tidy-up cancels ALL
resting orders on the symbol, including a concurrent run's.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle
    export VIPER_WALLET=0x...
    viper-examples stream-tpsl-watch

Tuning (optional env vars):
    VIPER_EXAMPLE_SYMBOL=BTC
    VIPER_EXAMPLE_USD=250
    VIPER_EXAMPLE_SECONDS=240     # overall ceiling (default 240)
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperWSClient, ViperRestClient, ViperError


ORDER = 32
KIND = "ws"
SECTION = "Streaming (WebSocket reads)"
DESCRIPTION = "Stream TP/SL watch lifecycle (tpsl.watch): queued -> placed, trails, per-leg failures."

_seen_placed: set = set()


def _render(frame: dict) -> None:
    if frame.get("channel") != "tpsl.watch":
        return
    ev = frame.get("event")
    d = frame.get("data") or {}
    wid = d.get("watch_id")
    if ev == "watch_snapshot":
        print(f"  [snapshot]  {len(d.get('watches') or [])} active watch(es)")
    elif ev == "hydrated":
        print("  [hydrated]  (snapshot complete; live events follow)")
    elif ev == "queued":
        print(f"  [queued]    watch={wid}  source={d.get('source')}  "
              f"source_id={d.get('source_id')}")
    elif ev == "placed":
        again = "  (SUPERSEDES earlier oids)" if wid in _seen_placed else ""
        _seen_placed.add(wid)
        print(f"  [placed]    watch={wid}  attached_oids={d.get('attached_oids')}"
              f"{again}")
        for leg in (d.get("legs") or []):
            res = leg.get("resolution") or {}
            print(f"              {leg.get('tpsl')}: trigger={leg.get('trigger_price')}"
                  + (f"  [{res.get('indicator')} -> {res.get('resolved_price')}]"
                     if res else ""))
        if d.get("partial_errors"):
            print(f"              partial_errors={d.get('partial_errors')}")
    elif ev == "cancelled":
        print(f"  [cancelled] watch={wid}  reason={d.get('reason')}")
    elif ev == "expired":
        print(f"  [expired]   watch={wid} — NO stop exists; action signal")
    elif ev == "failed":
        print(f"  [failed]    watch={wid}  reason={d.get('reason')} — "
              f"position may be UNPROTECTED")
    elif ev == "leg_failed":
        print(f"  [leg_failed] watch={wid}  {d.get('tpsl')}  "
              f"stage={d.get('stage')}  retrying={d.get('retrying')}  "
              f"error={d.get('error')}")
    elif ev == "leg_recovered":
        print(f"  [leg_recovered] watch={wid}  {d.get('tpsl')}  "
              f"after {d.get('after_failures')} failure(s)")
    elif ev == "trailed":
        print(f"  [trailed]   watch={wid}  {d.get('tpsl')}  "
              f"{d.get('old_trigger')} -> {d.get('new_trigger')}")


def _items(r):
    if isinstance(r, dict):
        return r.get("items") or r.get("orders") or r.get("positions") or []
    return r or []


async def main() -> None:
    wallet = os.environ.get("VIPER_WALLET", "").strip().lower()
    if not wallet:
        raise SystemExit("Set VIPER_WALLET to the wallet to stream.")
    symbol = os.environ.get("VIPER_EXAMPLE_SYMBOL", "BTC")
    usd = float(os.environ.get("VIPER_EXAMPLE_USD", "250"))
    ceiling = float(os.environ.get("VIPER_EXAMPLE_SECONDS", "240"))

    rest = ViperRestClient.from_env()
    ws = ViperWSClient.from_env(wallet=wallet, on_event=_render,
                                on_terminal=lambda c: None)
    eid = None
    async with rest:
        try:
            await ws.start()
            await ws.subscribe("tpsl.watch", wallet)
            print(f"subscribed tpsl.watch for {wallet[:10]}…\n")

            inst = (await rest.instrument(symbol)).get("instrument") or {}
            sz_decimals = inst.get("sz_decimals")
            price = await rest.price(symbol)
            mark = price.get("ask") or price.get("mark_price") or price.get("price")
            if not isinstance(sz_decimals, int) or not isinstance(mark, (int, float)):
                raise SystemExit(f"Could not size {symbol}: {inst!r} / {price!r}")
            q = 10 ** -sz_decimals
            size = max(q, ((usd / mark) // q) * q)

            r = await rest.execute(
                algo="glidemaker", symbol=symbol, side="buy", total_size=size,
                params={"strategy": "aggressive"},
                stop_loss={"offset": {"indicator": "atr", "mult": 2.0,
                                      "interval": "1h"}})
            eid = r.get("execution_id")
            print(f"launched {eid} with an ATR stop — watch the [queued] frame\n")

            # Wait for partial fills, then stop -> the [placed] frame arms.
            stopped = False
            polls = 0
            deadline = asyncio.get_event_loop().time() + ceiling
            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(5)
                polls += 1
                ex = await rest.execution(eid)
                st = (ex.get("state") or {}) if isinstance(ex.get("state"), dict) else ex
                filled = float(st.get("filled_size") or ex.get("filled_size") or 0)
                status = st.get("status") or ex.get("status")
                if not stopped and filled <= 0 and polls % 6 == 0:
                    print(f"  …waiting for fills (status={status}, quiet market)")
                if not stopped and filled > 0:
                    print(f"\npartial fill ({filled:g}) — stopping the algo; "
                          f"the stop arms sized to this fill\n")
                    await rest.cancel_execution(eid)
                    stopped = True
                    # Keep listening past the ceiling so the [placed] frame
                    # is caught even when the fill lands at ceiling-expiry.
                    deadline = max(deadline,
                                   asyncio.get_event_loop().time() + 20)
                if stopped and _seen_placed:
                    await asyncio.sleep(8)   # catch a possible re-size frame
                    break
                if status not in ("running", "pending", "paused") and not stopped:
                    break
            if not _seen_placed and stopped:
                print("  (no [placed] frame observed before exit — check the "
                      "venue for the trigger; the tidy-up below clears it)")
            elif not stopped:
                print("\nno fills within the ceiling — nothing to protect; "
                      "the watch retires at terminal. Tidying.")
        finally:
            try:
                await ws.close()
            except Exception:
                pass
            if eid:
                try:
                    await rest.cancel_execution(eid)
                except Exception:
                    pass
            await rest.cancel_all(symbol=symbol)
            try:
                await rest.close_position(symbol=symbol)
            except Exception:
                pass
            print("\ntidied: triggers cancelled, position closed.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ViperError as e:
        raise SystemExit(f"API error: {e}")

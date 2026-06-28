#!/usr/bin/env python3
"""
Stream one execution's lifecycle over WebSocket — the reliable per-execution channel.

execution.state is the per-execution-id stream: subscribe with an execution_id and
you get that execution's lifecycle, orders, and fills — hydration snapshot first,
then live events, then a terminal signal. (This is the per-id channel; it is the
reliable way to watch a single execution, distinct from the wallet-wide radar.)

What you'll see on the stream:
    execution_started   hydration snapshot of current state (on subscribe)
    state               rolling digest on each mutation — canonical status
                        (running / paused / stopped / error / completed),
                        filled_size, avg_fill_price, order_count
    order               per-order detail (resting / filled / cancelled)
    execution_ended     terminal signal — no frames after this for the exec_id

To have something to watch, this launches a passive Glidemaker (REST), then streams
its execution.state. A passive order that just rests is genuinely quiet — state/order
events fire on MUTATIONS, so to see lifecycle transitions, pair this with `algo-actions`
in another terminal (pause/resume/cancel the execution and watch the status change
here live). On its own, this run cancels the algo at the end so you see execution_ended.

Fires a REAL ~$250 Glidemaker (passive, far below market so it rests), streams it,
then cancels it. Nothing should fill.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle
    export VIPER_WALLET=0x...              # one wallet per connection
    viper-examples stream-execution

Tuning (optional env vars):
    VIPER_EXAMPLE_EXECUTION_ID=exec_...     # stream THIS execution instead of
                                            # launching one (pair with algo-actions)
    VIPER_EXAMPLE_COIN=BTC                  # symbol (default BTC)
    VIPER_EXAMPLE_USD=250                   # algo notional (default 250)
    VIPER_EXAMPLE_SECONDS=30                # how long to stream (default 30)
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperWSClient, ViperRestClient, ViperError


ORDER = 24
KIND = "ws"
SECTION = "Streaming (WebSocket reads)"
DESCRIPTION = "Stream one execution's lifecycle (execution.state) — the reliable per-execution channel."


def _render(frame: dict) -> None:
    if frame.get("channel") != "execution.state":
        return
    ev = frame.get("event")
    d = frame.get("data") or {}
    if ev == "execution_started":
        print(f"  [started]  algo={d.get('algo')}  status={d.get('status')}  "
              f"side={d.get('side')}  size={d.get('total_size')}")
    elif ev == "state":
        print(f"  [state]    status={d.get('status')}  "
              f"filled={d.get('filled_size')}  orders={d.get('order_count')}")
    elif ev == "order":
        print(f"  [order]    {d.get('side')} {d.get('size')} @ {d.get('price')}  "
              f"{d.get('status')}  oid={d.get('oid')}")
    elif ev == "execution_ended":
        print(f"  [ended]    status={d.get('status')}  "
              f"filled={d.get('filled_size')}/{d.get('total_size')}  "
              f"reason={d.get('reason')}")
    elif ev == "hydrated":
        print("  [hydrated] (snapshot complete; live events follow)")


async def main() -> None:
    wallet = os.environ.get("VIPER_WALLET", "").strip().lower()
    if not wallet:
        raise SystemExit("Set VIPER_WALLET to the wallet to use.")
    coin = os.environ.get("VIPER_EXAMPLE_COIN", "BTC")
    usd = float(os.environ.get("VIPER_EXAMPLE_USD", "250"))
    seconds = float(os.environ.get("VIPER_EXAMPLE_SECONDS", "30"))

    ended = asyncio.Event()

    def on_event(frame):
        _render(frame)
        if frame.get("channel") == "execution.state" and frame.get("event") == "execution_ended":
            ended.set()

    def on_terminal(code):
        print(f"  (connection closed: {code})")
        ended.set()

    rest = ViperRestClient.from_env()
    await rest.__aenter__()
    exec_id = None
    launched_here = False
    try:
        # If an execution_id is provided, stream THAT (pair with algo-actions in
        # another terminal). Otherwise launch our own passive Glidemaker to watch.
        exec_id = (os.environ.get("VIPER_EXAMPLE_EXECUTION_ID", "") or "").strip() or None
        if exec_id:
            print(f"# streaming execution.state for {exec_id} (provided)\n")
        else:
            px = await rest.price(coin)
            mark = px.get("mid") or px.get("mark") or px.get("price")
            if not isinstance(mark, (int, float)) or mark <= 0:
                raise SystemExit(f"could not read a mark for {coin}: {px!r}")
            size = max(round(usd / mark, 5), 0.00001)
            limit = round(mark * 0.80)
            started = await rest.execute(algo="glidemaker", symbol=coin, side="buy",
                                        total_size=size,
                                        params={"strategy": "passive", "limit_price": limit,
                                                "post_only": True})
            exec_id = started.get("execution_id")
            launched_here = True
            print(f"# streaming execution.state for {exec_id} ({coin})\n")
        if not exec_id:
            return

        client = ViperWSClient.from_env(wallet=wallet, on_event=on_event,
                                        on_terminal=on_terminal)
        await client.start()
        try:
            await client.subscribe("execution.state", exec_id)
            # Stream until terminal or the time window elapses.
            try:
                await asyncio.wait_for(ended.wait(), timeout=seconds)
            except asyncio.TimeoutError:
                print(f"  (stream window of {seconds:g}s elapsed)")
            # If it hasn't ended yet, cancel it WHILE the socket is open so we
            # actually observe the execution_ended frame — but only if WE launched
            # it. If streaming an external execution (algo-actions drives it), just
            # observe; that terminal owns the lifecycle.
            if not ended.is_set() and launched_here:
                print("  (cancelling — watch for [ended])")
                try:
                    await rest.cancel_execution(exec_id)
                    exec_id = None  # cancelled; don't re-cancel in finally
                except Exception:
                    pass
                try:
                    await asyncio.wait_for(ended.wait(), timeout=10)
                except asyncio.TimeoutError:
                    print("  (no execution_ended within 10s)")
        finally:
            await client.close()
    except ViperError as e:
        print(f"API error: {e}")
    finally:
        # Cleanup: cancel the execution ONLY if we launched it here.
        if exec_id and launched_here:
            try:
                await rest.cancel_execution(exec_id)
                print("  (cancelled the demo execution)")
            except Exception:
                pass
        await rest.__aexit__(None, None, None)


if __name__ == "__main__":
    asyncio.run(main())

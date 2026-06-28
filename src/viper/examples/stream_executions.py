#!/usr/bin/env python3
"""
Watch ALL your executions live, with full per-algo detail — including ones that
start after you're already streaming.

The pattern every execution-monitoring bot needs:

    execution.list   (wallet-scoped)  is the radar — it fires execution_started
                     the moment ANY algo launches on your wallet, and
                     execution_ended when one finishes.
    execution.state  (exec_id-scoped) is the detail — per-execution orders, fills,
                     and the algo type.

This subscribes to execution.list, and whenever a new execution starts, it
auto-subscribes to that execution's execution.state — so you get full detail for
every algo, including ones launched later, without restarting. That's the piece a
flat single-subscription stream can't do: new exec_ids don't exist yet when you
start, so you discover them via execution.list and subscribe on the fly.

Pair it with anything that launches algos (the start-* examples, monitor-actions,
the UI, webhooks) and watch them appear with their algo type and fills.

Read-only — it places nothing. (execution.list is the wallet-wide radar; the
per-execution order/fill detail lives on execution.state, the per-id channel, so
each new execution is auto-subscribed there for its detail.)

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle        # optional
    export VIPER_WALLET=0x...              # the wallet to watch
    viper-examples stream-executions

Tuning (optional env vars):
    VIPER_EXAMPLE_SECONDS=180    # how long to watch (default 180)
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperWSClient

ORDER = 25
KIND = "ws"
SECTION = "Streaming (WebSocket reads)"
DESCRIPTION = "Watch all executions live (execution.list radar + auto-subscribed execution.state detail)."


def main_factory():
    # Track which exec_ids we've already subscribed to (avoid double-subscribe)
    # and remember each one's algo type from its execution.state started frame.
    known: set[str] = set()
    algo_of: dict[str, str] = {}
    return known, algo_of


async def main() -> None:
    wallet = os.environ.get("VIPER_WALLET", "").strip().lower()
    if not wallet:
        raise SystemExit("Set VIPER_WALLET to the wallet you want to watch.")
    seconds = float(os.environ.get("VIPER_EXAMPLE_SECONDS", "180"))

    known, algo_of = main_factory()

    def on_event(frame):
        ch = frame.get("channel")
        ev = frame.get("event")
        d = frame.get("data") or {}
        owner = d.get("wallet") or frame.get("scope_id")
        if owner and owner.lower() != wallet:
            return

        if ch == "execution.list":
            eid = d.get("execution_id")
            if ev == "execution_started" and eid and eid not in known:
                known.add(eid)
                # Subscribe to this execution's detail IMMEDIATELY (schedule the
                # coroutine now) rather than deferring — a batch of launches fires
                # many list events at once, and any delay risks missing the
                # execution.state started frame for the fast ones.
                asyncio.create_task(
                    client.subscribe("execution.state", eid, cadence_bearing=True))
                print(f"[list] STARTED  {d.get('symbol')} {d.get('side')}  id={eid}  "
                      f"-> subscribing to its detail")
            elif ev == "execution_ended" and eid:
                algo = algo_of.get(eid, "?")
                print(f"[list] ENDED    {algo} {d.get('symbol')}  status={d.get('status')}  id={eid}")

        elif ch == "execution.state":
            eid = d.get("execution_id") or frame.get("scope_id")
            if ev == "execution_started":
                algo = d.get("algo")                # execution.state carries the algo type
                if eid:
                    algo_of[eid] = algo
                print(f"  [{algo}] started  {d.get('symbol')} {d.get('side')} "
                      f"size={d.get('total_size')} status={d.get('status')}")
            elif ev == "order":
                algo = algo_of.get(eid, "")
                status = d.get("status")
                # maker/taker only describes an order that rests or fills; a
                # rejected or cancelled order never took liquidity, so omit the
                # tag for those (post-only rejects are normal and would otherwise
                # render misleadingly as "taker").
                liq = "" if status in ("rejected", "cancelled") else (
                    " maker" if d.get("is_maker") else " taker")
                print(f"  [{algo}] order   {d.get('side')} {d.get('size')} @ {d.get('price')} "
                      f"{status} filled={d.get('filled_size')}{liq}")
            elif ev == "state":
                algo = algo_of.get(eid, "")
                print(f"  [{algo}] state   filled={d.get('filled_size')} "
                      f"avg={d.get('avg_fill_price')} orders={d.get('order_count')}")
            elif ev == "execution_ended":
                algo = algo_of.get(eid, "")
                print(f"  [{algo}] ended   filled={d.get('filled_size')}/{d.get('total_size')} "
                      f"status={d.get('status')} reason={d.get('reason')}")

    def on_terminal(code):
        print(f"# terminal close (code={code}) — stopping")

    client = ViperWSClient.from_env(wallet=wallet, on_event=on_event,
                                    on_terminal=on_terminal)
    print(f"# watching all executions for {wallet} for {seconds:g}s\n"
          f"#   execution.list (radar) + auto-subscribed execution.state (detail)\n"
          f"#   launch algos elsewhere and watch them appear\n")
    await client.start()
    await client.subscribe("execution.list", wallet)
    await asyncio.sleep(seconds)
    await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        raise SystemExit(f"error: {e}")

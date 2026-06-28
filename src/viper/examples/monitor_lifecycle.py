#!/usr/bin/env python3
"""
Create a monitor, fire its algo on trigger, re-arm, and tear it down.

Monitors watch market conditions and fire an action when their threshold is met.
The useful case is action=algo_execution: when the monitor fires, it launches one
of Viper's execution algos. This example walks that lifecycle over REST:

  create   a large_trade monitor whose action launches a Glidemaker, with an
           unreachable threshold so it only fires when WE trigger it (not on real flow)
  inspect  its state and live-stats ("why not triggered")
  pause    disable/enable while it's armed (the pause/resume toggle)
  trigger  it manually -> fires the algo; a single-fire monitor goes to 'complete'
  executions read what the monitor fired
  re-arm   reset a fired ('complete') monitor back to 'active'
  delete   remove the monitor, then cancel/close anything the fired algo left

Triggering launches a REAL Glidemaker (~$250, passive). The example cancels and
closes afterward, but it does place a live algo. There is no testnet.

Uses the client's typed monitor methods (create_monitor, trigger_monitor,
enable_monitor, reset_monitor, monitor_live_stats, monitor_executions, ...).

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle        # optional
    viper-examples monitor-lifecycle

Tuning (optional env vars):
    VIPER_EXAMPLE_COIN=BTC       # coin to watch (default BTC)
    VIPER_EXAMPLE_ALGO_USD=250   # algo notional in USD (default 250; algo min is $250)
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperRestClient, ViperError

ORDER = 18
SECTION = "Monitors"
DESCRIPTION = "Create a monitor, fire its algo on trigger, re-arm, and delete it."

# Unreachable trade value so the monitor only fires when we trigger it.
_UNREACHABLE = 50_000_000.0


async def main() -> None:
    coin = os.environ.get("VIPER_EXAMPLE_COIN", "BTC")
    algo_usd = float(os.environ.get("VIPER_EXAMPLE_ALGO_USD", "250"))

    rest = ViperRestClient.from_env()
    async with rest:
        # 1) CREATE — action=algo_execution: fires a Glidemaker when triggered.
        #    action enum is: alert | market_order | limit_order | algo_execution.
        print("1) CREATE (action=algo_execution -> Glidemaker, unreachable threshold)")
        created = await rest.create_monitor(
            name="sdk example monitor",
            type="large_trade",
            coins=[coin],
            value_threshold=_UNREACHABLE,
            side="any",
            cooldown_seconds=30,
            fire_mode="single",
            enabled=True,
            action="algo_execution",
            order_action={
                "side": "buy",
                "size_type": "fixed_usd",
                "size": algo_usd,
                "algo_type": "glidemaker",
                "algo_strategy": "passive",
                "post_only": True,
            },
        )
        mid = created.get("id")
        if not mid:
            raise SystemExit(f"No monitor id in create response: {created!r}")
        oa = created.get("order_action") or {}
        print(f"   id={mid}  state={created.get('state')}  action={created.get('action')}")
        print(f"   fires: {oa.get('algo_type')} {oa.get('side')} "
              f"${oa.get('size')} ({oa.get('algo_strategy')})")

        algo_exec_id = None
        try:
            # 2) INSPECT — live-stats explains why it hasn't fired.
            print("\n2) INSPECT (live-stats)")
            ls = await rest.monitor_live_stats(mid)
            row = ls if ls.get("monitor_id") else ((ls.get("items") or [{}])[0])
            print(f"   state={row.get('state')}  fire_count={row.get('fire_count')}  "
                  f"readiness={row.get('trigger_readiness_pct')}%")
            if row.get("why_not_triggered"):
                print(f"   why_not_triggered: {row.get('why_not_triggered')}")

            # 3) PAUSE / RESUME — while the monitor is armed (active), enable=false
            #    pauses it and enable=true resumes. (This toggle only works on an
            #    armed monitor — once it FIRES it goes to 'complete', and the
            #    re-arm path is reset, shown in step 6.)
            print("\n3) PAUSE / RESUME (while armed)")
            await rest.enable_monitor(mid, enabled=False)
            off = await rest.monitor(mid)
            print(f"   paused  -> enabled={off.get('enabled')} state={off.get('state')}")
            await rest.enable_monitor(mid, enabled=True)
            on = await rest.monitor(mid)
            print(f"   resumed -> enabled={on.get('enabled')} state={on.get('state')}")

            # 4) TRIGGER — manual fire -> launches the Glidemaker. A single-fire
            #    monitor moves to state='complete' after firing.
            print("\n4) TRIGGER (fires the algo)")
            trig = await rest.trigger_monitor(mid)
            ar = trig.get("action_result") or {}
            algo_exec_id = ar.get("algo_execution_id")
            print(f"   success={ar.get('success')}  resolved_side={ar.get('resolved_side')}  "
                  f"order_size={ar.get('order_size')}")
            print(f"   spawned execution: {algo_exec_id}")

            # 5) EXECUTIONS — read what the monitor fired (the monitor-scoped
            #    executions endpoint returns the fire record + result).
            print("\n5) EXECUTIONS (what the monitor fired)")
            await asyncio.sleep(1)
            ex = await rest.monitor_executions(mid)
            items = ex.get("items") or []
            if items:
                rec = items[0]
                res = rec.get("result") or {}
                print(f"   fire_count={(rec.get('monitor') or {}).get('fire_count')}  "
                      f"algo_execution_id={res.get('algo_execution_id')}  "
                      f"success={res.get('success')}")
            else:
                print(f"   (no execution records yet: {ex})")

            # 6) RE-ARM — after firing, the monitor is 'complete'; reset re-arms it
            #    back to 'active'. (enable=true is rejected on a complete monitor.)
            print("\n6) RE-ARM (reset a fired monitor back to active)")
            await rest.reset_monitor(mid)
            armed = await rest.monitor(mid)
            print(f"   reset -> state={armed.get('state')} fire_count={armed.get('fire_count')}")

        finally:
            # 7) DELETE the monitor, then cancel/close whatever the algo left.
            print("\n7) DELETE + cleanup")
            # stop the fired algo first (cancelling its resting order alone won't
            # stop the execution), then delete the monitor and flatten.
            if algo_exec_id:
                try:
                    await rest.cancel_execution(algo_exec_id)
                except ViperError:
                    pass
            try:
                await rest.delete_monitor(mid)
                print("   monitor deleted.")
            except ViperError as e:
                print(f"   monitor delete failed: {e}")
            try:
                await rest.cancel_all(symbol=coin)
                closed = await rest.close_position(symbol=coin)
                fs = closed.get("filled_size") if isinstance(closed, dict) else None
                print(f"   cancelled orders; closed {fs if fs is not None else 'nothing'}.")
            except ViperError as e:
                print(f"   cleanup note: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ViperError as e:
        raise SystemExit(f"API error: {e}")

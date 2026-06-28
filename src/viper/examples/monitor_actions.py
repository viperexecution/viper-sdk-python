#!/usr/bin/env python3
"""
Drive a monitor through its lifecycle, narrating each action (a driver for the
`stream-monitors` example — run this in one terminal, stream in another).

Like `place-order`, this performs the actions; pair it with `stream-monitors` to
watch the matching WS frames. It:

  creates  an algo_execution monitor (fires a Glidemaker when triggered), prints
           its id, and PAUSES so you can start the stream on that id
  pauses / resumes  the monitor (state_change armed/paused)
  triggers it       (fires the algo; monitor.event order_submitted/order_success)
  resets it         (re-arms a completed monitor)
  deletes it        and cleans up the fired algo

Triggering fires a REAL Glidemaker (~$250, passive); it's cancelled/closed after.

Two-terminal flow:
    # terminal 1 — start this; copy the printed monitor id; press Enter when streaming
    viper-examples monitor-actions
    # terminal 2 — stream that id
    export VIPER_WALLET=0x...
    export VIPER_EXAMPLE_MONITOR_ID=mon_...   # the id terminal 1 printed
    viper-examples stream-monitors

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle        # optional
    viper-examples monitor-actions

Tuning (optional env vars):
    VIPER_EXAMPLE_COIN=BTC        # coin to watch (default BTC)
    VIPER_EXAMPLE_ALGO_USD=250    # algo notional (default 250)
    VIPER_EXAMPLE_PAUSE=1         # 1 to wait for Enter before driving (default 1)
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperRestClient, ViperError

ORDER = 19
SECTION = "Monitors"
DESCRIPTION = "Drive a monitor's lifecycle (create/pause/trigger/reset) — pair with stream-monitors."

_UNREACHABLE = 50_000_000.0


async def main() -> None:
    coin = os.environ.get("VIPER_EXAMPLE_COIN", "BTC")
    algo_usd = float(os.environ.get("VIPER_EXAMPLE_ALGO_USD", "250"))
    want_pause = os.environ.get("VIPER_EXAMPLE_PAUSE", "1") == "1"

    rest = ViperRestClient.from_env()
    async with rest:
        # CREATE
        created = await rest.create_monitor(
            name="sdk monitor-actions", type="large_trade", coins=[coin],
            value_threshold=_UNREACHABLE, side="any", cooldown_seconds=30,
            fire_mode="single", enabled=True, action="algo_execution",
            order_action={"side": "buy", "size_type": "fixed_usd", "size": algo_usd,
                          "algo_type": "glidemaker", "algo_strategy": "passive",
                          "post_only": True},
        )
        mid = created.get("id")
        if not mid:
            raise SystemExit(f"create failed: {created!r}")
        print(f"CREATED monitor: {mid}")
        print(f"  fires {coin} glidemaker buy ${algo_usd:g} when triggered\n")

        algo_exec_id = None
        try:
            if want_pause:
                print(">>> Start the stream on this id now, then press Enter to drive it.")
                print(f">>>   VIPER_EXAMPLE_MONITOR_ID={mid}")
                await asyncio.get_event_loop().run_in_executor(None, input)

            print("PAUSE  (enable=false)")
            await rest.enable_monitor(mid, enabled=False)
            await asyncio.sleep(3)

            print("RESUME (enable=true)")
            await rest.enable_monitor(mid, enabled=True)
            await asyncio.sleep(3)

            print("TRIGGER (fires the algo)")
            trig = await rest.trigger_monitor(mid)
            ar = trig.get("action_result") or {}
            algo_exec_id = ar.get("algo_execution_id")
            print(f"  -> success={ar.get('success')} exec={algo_exec_id}")
            await asyncio.sleep(5)

            print("RESET (re-arm the completed monitor)")
            await rest.reset_monitor(mid)
            armed = await rest.monitor(mid)
            print(f"  -> state={armed.get('state')} fire_count={armed.get('fire_count')}")
            await asyncio.sleep(2)

        finally:
            print("\nDELETE + cleanup")
            # stop the fired algo first (cancelling its resting order alone won't
            # stop the execution), then delete the monitor and flatten.
            if algo_exec_id:
                try:
                    await rest.cancel_execution(algo_exec_id)
                    print(f"  algo {algo_exec_id} stopped.")
                except ViperError as e:
                    print(f"  algo stop note: {e}")
            try:
                await rest.delete_monitor(mid)
                print("  monitor deleted.")
            except ViperError as e:
                print(f"  delete failed: {e}")
            try:
                await rest.cancel_all(symbol=coin)
                closed = await rest.close_position(symbol=coin)
                fs = closed.get("filled_size") if isinstance(closed, dict) else None
                print(f"  cancelled orders; closed {fs if fs is not None else 'nothing'}.")
            except ViperError as e:
                print(f"  cleanup note: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ViperError as e:
        raise SystemExit(f"API error: {e}")

#!/usr/bin/env python3
"""
Stream a monitor's lifecycle live over WebSocket (monitor.* channels).

Monitors emit on four channels, scoped by MONITOR ID:
    monitor.event         lifecycle narrative — armed, triggered, fired, paused,
                          resumed, reset, completed (each frame carries a ready
                          human-readable `message`)
    monitor.state_change  state-machine transitions (armed / paused / ...)
    monitor.stats         live metric stream (readiness %, why-not-triggered,
                          rolling window) — emitted frequently
    monitor.alert         alert raised when the monitor fires

Pair this with `monitor-lifecycle` (or the dashboard): start this stream on a
monitor id, then trigger / pause / reset that monitor elsewhere and watch the
events arrive.

Two ways to run:
  - Self-contained (default): run with no monitor id and it creates its own
    monitor, streams it, drives it (pause -> resume -> trigger -> reset), then
    deletes it and stops the algo. Triggering fires a real Glidemaker (~$250),
    which is stopped and flattened on the way out.
  - Watch an existing monitor: set VIPER_EXAMPLE_MONITOR_ID and drive that monitor
    from elsewhere (the app, or the `monitor-actions` example in another terminal).

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle              # optional
    export VIPER_WALLET=0x...                     # your wallet
    viper-examples stream-monitors               # self-contained demo

Tuning (optional env vars):
    VIPER_EXAMPLE_MONITOR_ID=mon_...   # watch this monitor instead of self-driving
    VIPER_EXAMPLE_SECONDS=60           # stream duration when watching (default 60)
    VIPER_EXAMPLE_STATS=0              # 1 to print every stats_update (default: summarize)
    VIPER_EXAMPLE_ALGO_USD=250         # algo notional when self-driving (default 250)
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperWSClient, ViperRestClient, ViperError

ORDER = 22
KIND = "ws"
SECTION = "Streaming (WebSocket reads)"
DESCRIPTION = "Stream a monitor's lifecycle (monitor.* channels) live over WebSocket."

_CHANNELS = ("monitor.event", "monitor.state_change", "monitor.stats", "monitor.alert")


async def _create_monitor(rest, coin, algo_usd):
    _UNREACHABLE = 50_000_000.0
    created = await rest.create_monitor(
        name="sdk stream-monitors", type="large_trade", coins=[coin],
        value_threshold=_UNREACHABLE, side="any", cooldown_seconds=30,
        fire_mode="single", enabled=True, action="algo_execution",
        order_action={"side": "buy", "size_type": "fixed_usd", "size": algo_usd,
                      "algo_type": "glidemaker", "algo_strategy": "passive",
                      "post_only": True},
    )
    return created.get("id")


async def _drive(rest, mid, coin):
    """Walk a monitor through its lifecycle so the stream shows every event.
    Returns the algo_execution_id the trigger spawned (for cleanup)."""
    await asyncio.sleep(3)                              # let stream hydrate
    await rest.enable_monitor(mid, enabled=False)       # -> paused
    await asyncio.sleep(3)
    await rest.enable_monitor(mid, enabled=True)        # -> armed
    await asyncio.sleep(3)
    trig = await rest.trigger_monitor(mid)              # -> fires algo, completed
    algo_exec_id = (trig.get("action_result") or {}).get("algo_execution_id")
    await asyncio.sleep(5)
    await rest.reset_monitor(mid)                       # -> re-armed
    await asyncio.sleep(3)
    return algo_exec_id


async def _cleanup(rest, mid, coin, algo_exec_id=None):
    # Stop the fired algo FIRST (cancelling its resting order alone won't stop it —
    # the execution would just place the next one), then clear orders + flatten.
    if algo_exec_id:
        try:
            await rest.cancel_execution(algo_exec_id)
        except ViperError:
            pass
    try:
        await rest.delete_monitor(mid)
    except ViperError:
        pass
    try:
        await rest.cancel_all(symbol=coin)
        await rest.close_position(symbol=coin)
    except ViperError:
        pass


def _render(frame: dict) -> None:
    ch = frame.get("channel")
    ev = frame.get("event")
    d = frame.get("data") or {}

    # hydration / ephemeral markers
    if ev in ("hydrated", "state_snapshot", "stats_snapshot"):
        return

    if ch == "monitor.event":
        # Every monitor.event frame carries a ready-made human message.
        msg = d.get("message") or d.get("type") or ev
        print(f"  event   {ev:18} {msg}")

    elif ch == "monitor.state_change":
        print(f"  state   {d.get('state', ev)}  (enabled={d.get('enabled')})")

    elif ch == "monitor.alert":
        # alert frames carry the trigger type / details
        msg = d.get("message") or d.get("type") or ev
        print(f"  ALERT   {msg}")

    elif ch == "monitor.stats":
        # High-frequency live stats — summarize to the bits that change.
        rd = d.get("trigger_readiness_pct")
        why = d.get("why_not_triggered")
        print(f"  stats   state={d.get('state')} readiness={rd}%"
              + (f"  ({why})" if why else ""))

    else:
        print(f"  {ch}/{ev}  {d}")


async def main() -> None:
    wallet = os.environ.get("VIPER_WALLET", "").strip().lower()
    monitor_id = os.environ.get("VIPER_EXAMPLE_MONITOR_ID", "").strip()
    seconds = float(os.environ.get("VIPER_EXAMPLE_SECONDS", "60"))
    show_stats = os.environ.get("VIPER_EXAMPLE_STATS", "0") == "1"
    coin = os.environ.get("VIPER_EXAMPLE_COIN", "BTC")
    algo_usd = float(os.environ.get("VIPER_EXAMPLE_ALGO_USD", "250"))

    if not wallet:
        raise SystemExit("Set VIPER_WALLET to your wallet.")

    if monitor_id in ("mon_...", "mon_", "...", "<id>"):
        raise SystemExit(f"VIPER_EXAMPLE_MONITOR_ID is a placeholder ({monitor_id!r}); "
                         f"set it to a real monitor id, or unset it to self-drive.")

    # No id given -> self-drive: create our own monitor, stream it, drive it,
    # and tear it down. With an id -> just watch that monitor (drive it elsewhere).
    created_here = not monitor_id
    if created_here:
        rest = ViperRestClient.from_env()
        async with rest:
            monitor_id = await _create_monitor(rest, coin, algo_usd)
        if not monitor_id:
            raise SystemExit("Could not create a monitor to drive.")
        print(f"# created monitor {monitor_id} — will drive pause/trigger/reset and "
              f"delete it (fires a real ~${algo_usd:g} Glidemaker, then stops it)\n")

    def on_event(frame):
        if frame.get("channel") == "monitor.stats" and not show_stats:
            if frame.get("event") == "stats_update":
                return
        _render(frame)

    def on_terminal(code):
        print(f"# terminal close (code={code}) — stopping")

    client = ViperWSClient.from_env(wallet=wallet, on_event=on_event,
                                    on_terminal=on_terminal)
    if not created_here:
        print(f"# streaming monitor {monitor_id} for {seconds:g}s — "
              f"drive it elsewhere to see events"
              + ("" if show_stats else "  (stats summarized; VIPER_EXAMPLE_STATS=1 for all)") + "\n")

    await client.start()
    for ch in _CHANNELS:                          # monitor.* are scoped by MONITOR ID
        await client.subscribe(ch, monitor_id)

    if created_here:
        # Drive the lifecycle and stream it concurrently, then clean up.
        rest = ViperRestClient.from_env()
        async with rest:
            algo_exec_id = await _drive(rest, monitor_id, coin)
            await asyncio.sleep(2)
            await client.close()
            await _cleanup(rest, monitor_id, coin, algo_exec_id)
            print("\n# monitor deleted, algo stopped, orders/positions cleaned up.")
    else:
        await asyncio.sleep(seconds)
        await client.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ViperError as e:
        raise SystemExit(f"API error: {e}")

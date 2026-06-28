#!/usr/bin/env python3
"""
Manage your whole fleet over the socket — executions AND monitors, all at once.

A fleet is more than running algos: it's executions plus Trade Monitors. To truly
freeze or kill everything, you bulk-control both — pause_all_executions does NOT
touch monitors, and vice versa. This shows the complete picture:

    executions: pause_all_executions / resume_all_executions / cancel_all_executions
    monitors:   pause_all_monitors / resume_all_monitors / stop_all_monitors

(Monitors are created over REST — per-monitor create is REST-only — but the bulk
CONTROL is over the socket, same as executions. The example creates a couple of
monitors up front so the monitor bulk ops have something to act on.)

Scope: every bulk command takes an optional `scope` —
    "wallet"  (DEFAULT) only this connection's wallet — the safe default
    "account"            every wallet on the account
The WS default ("wallet") is the OPPOSITE of the REST bulk endpoints (which
default account-wide); the socket is already wallet-bound, so the safe WS default
honors that. This example uses the default (wallet).

Fires REAL algos (~$250 each, passive + far below market so they rest) and creates
alert-only monitors (no orders). cancel_all + stop_all are the cleanup.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle        # optional
    export VIPER_WALLET=0x...              # one wallet per connection
    viper-examples ws-bulk-control

Tuning (optional env vars):
    VIPER_EXAMPLE_COIN=BTC                  # symbol (default BTC)
    VIPER_EXAMPLE_USD=250                   # per-algo notional (default 250)
"""
from __future__ import annotations

import os
import uuid
import asyncio

from viper import ViperWSClient, ViperRestClient

ORDER = 31
KIND = "ws"
SECTION = "Trading over WebSocket (Tier-3 writes)"
DESCRIPTION = "Bulk-control the whole fleet over the socket (executions + monitors: pause/resume/cancel all)."


def _ik() -> str:
    return uuid.uuid4().hex


async def _cmd(client, command: str, **fields):
    frame = {"command": command, **fields}
    r = await client.send_command(frame, wait=20.0)
    if r is None:
        print(f"  {command}: (no response within timeout)")
        return None
    if r.get("result") == "error":
        print(f"  {command}: ERROR {r.get('code')}: {r.get('message')}")
        return None
    return r


def _bulk_line(r) -> str:
    d = r.get("data") or {}
    return (f"result={r.get('result')}  action={d.get('action')}  "
            f"count={d.get('count')}  errors={d.get('error_count')}")


async def main() -> None:
    wallet = os.environ.get("VIPER_WALLET", "").strip().lower()
    if not wallet:
        raise SystemExit("Set VIPER_WALLET to the wallet to trade on.")
    coin = os.environ.get("VIPER_EXAMPLE_COIN", "BTC")
    usd = float(os.environ.get("VIPER_EXAMPLE_USD", "250"))

    async with ViperRestClient.from_env() as rest_probe:
        px = await rest_probe.price(coin)
        mark = float(px.get("mid") or px.get("mark") or px.get("price") or 0)
    if mark <= 0:
        raise SystemExit(f"could not read a mark for {coin}: {px!r}")
    size = max(round(usd / mark, 5), 0.00001)
    far = round(mark * 0.80)

    client = ViperWSClient.from_env(wallet=wallet, on_event=lambda f: None,
                                    on_terminal=lambda c: None)
    print(f"# fleet bulk control over one WS connection ({wallet}, {coin}, mark={mark})\n")
    await client.start()

    # We'll create monitors over REST (per-monitor create is REST-only) so the
    # monitor bulk ops have something to act on.
    rest = ViperRestClient.from_env()
    monitor_ids = []
    try:
        await rest.__aenter__()

        # 1a) LAUNCH 3 algos (WS) — glidemaker + pacemaker + glidemaker (pause-clean).
        print("1) SET UP FLEET")
        print("   algos (WS): glidemaker / pacemaker / glidemaker")
        for algo, params in (
            ("glidemaker", {"strategy": "passive", "limit_price": far, "post_only": True}),
            ("pacemaker", {"duration_seconds": 300, "urgency": "normal"}),
            ("glidemaker", {"strategy": "passive", "limit_price": round(mark * 0.78),
                            "post_only": True}),
        ):
            r = await _cmd(client, "start_algo", idempotency_key=_ik(),
                          symbol=coin, side="buy", total_size=size, algo=algo,
                          params=params, reduce_only=False)
            if r:
                print(f"     started {algo}: {(r.get('data') or {}).get('execution_id')}")

        # 1b) CREATE 2 monitors (REST) — alert-only, unreachable threshold (no fires).
        print("   monitors (REST): 2 alert-only")
        for i in range(2):
            m = await rest.create_monitor(
                name=f"sdk bulk example {i}", type="large_trade", coins=[coin],
                value_threshold=50_000_000.0, action="alert", side="any",
                cooldown_seconds=30, fire_mode="single", enabled=True)
            mid = m.get("id")
            if mid:
                monitor_ids.append(mid)
                print(f"     created monitor: {mid}")
        await asyncio.sleep(3)

        # 2) PAUSE ALL — executions AND monitors (scope=wallet default).
        print("\n2) PAUSE ALL  (executions + monitors, scope=wallet)")
        r = await _cmd(client, "pause_all_executions", scope="wallet")
        if r:
            print(f"   executions: {_bulk_line(r)}")
        r = await _cmd(client, "pause_all_monitors", scope="wallet")
        if r:
            d = r.get("data") or {}
            print(f"   monitors:   result={r.get('result')}  "
                  f"paused_count={d.get('paused_count')}")
        await asyncio.sleep(2)

        # 3) RESUME ALL — both.
        print("\n3) RESUME ALL  (executions + monitors)")
        r = await _cmd(client, "resume_all_executions", scope="wallet")
        if r:
            print(f"   executions: {_bulk_line(r)}")
        r = await _cmd(client, "resume_all_monitors", scope="wallet")
        if r:
            d = r.get("data") or {}
            print(f"   monitors:   result={r.get('result')}  "
                  f"resumed_count={d.get('resumed_count')}  "
                  f"skipped={d.get('skipped_count')}")
        await asyncio.sleep(2)

    finally:
        # 4) KILL ALL — cancel executions + stop monitors (the full fleet kill).
        print("\n4) KILL ALL  (cancel executions + stop monitors)")
        r = await _cmd(client, "cancel_all_executions", scope="wallet")
        if r:
            print(f"   executions: {_bulk_line(r)}")
        r = await _cmd(client, "stop_all_monitors", scope="wallet")
        if r:
            d = r.get("data") or {}
            print(f"   monitors:   result={r.get('result')}  "
                  f"stopped_count={d.get('stopped_count')}")
        # delete the monitors we created (stop is terminal but leaves them listed)
        for mid in monitor_ids:
            try:
                await rest.delete_monitor(mid)
            except Exception:
                pass
        try:
            await rest.__aexit__(None, None, None)
        except Exception:
            pass
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())

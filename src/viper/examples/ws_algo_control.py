#!/usr/bin/env python3
"""
Drive a live algo entirely over the socket — start, pause, retune, resume, cancel.

The Tier-3 capstone: launch an execution and control its whole lifecycle over a
single WebSocket connection, no REST calls. This is the pattern for a bot that
holds one socket and manages its algos through it.

    start_algo              launch a Glidemaker (passive limit)
    pause_execution         pause it
    patch_execution_params  retune it while paused (limit_price) — patch REQUIRES
                            the execution be paused first
    resume_execution        resume
    cancel_execution        stop it (with a reason)

Verified command facts (from the streams Tier-3 contract):
  - patch_execution_params requires the execution be PAUSED, and `params` may only
    contain fields editable for that algo (Glidemaker: limit_price, would_fill_price).
  - each command's result is a literal: paused / patched / resumed / cancelled.
  - mutating commands need an idempotency_key (you supply it).

This fires a REAL ~$250 Glidemaker (passive, post-only, far below market so it
rests), then cancels it. Nothing should fill.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle        # optional
    export VIPER_WALLET=0x...              # one wallet per connection
    viper-examples ws-algo-control

Tuning (optional env vars):
    VIPER_EXAMPLE_COIN=BTC                  # symbol (default BTC)
    VIPER_EXAMPLE_USD=250                   # algo notional in USD (default 250)
"""
from __future__ import annotations

import os
import uuid
import asyncio

from viper import ViperWSClient, ViperRestClient

ORDER = 28
KIND = "ws"
SECTION = "Trading over WebSocket (Tier-3 writes)"
DESCRIPTION = "Drive a live algo over the socket (Tier-3: start/pause/patch/resume/cancel)."


def _ik() -> str:
    return uuid.uuid4().hex


async def _cmd(client, command: str, **fields):
    frame = {"command": command, **fields}
    r = await client.send_command(frame)
    if r is None:
        print(f"  {command}: (no response within timeout)")
        return None
    if r.get("result") == "error":
        print(f"  {command}: ERROR {r.get('code')}: {r.get('message')}")
        return None
    return r


async def main() -> None:
    wallet = os.environ.get("VIPER_WALLET", "").strip().lower()
    if not wallet:
        raise SystemExit("Set VIPER_WALLET to the wallet to trade on.")
    coin = os.environ.get("VIPER_EXAMPLE_COIN", "BTC")
    usd = float(os.environ.get("VIPER_EXAMPLE_USD", "250"))

    # One REST call up front: read the mark to size the algo and pick a passive
    # limit far below market (so the Glidemaker rests rather than fills). Every
    # lifecycle command after this happens over the socket.
    async with ViperRestClient.from_env() as rest:
        px = await rest.price(coin)
        mark = float(px.get("mid") or px.get("mark") or px.get("price") or 0)
    if mark <= 0:
        raise SystemExit(f"could not read a mark for {coin}: {px!r}")
    size = max(round(usd / mark, 5), 0.00001)
    limit_1 = round(mark * 0.80)             # initial passive limit (rests)
    limit_2 = round(mark * 0.78)             # retuned limit (still rests)

    client = ViperWSClient.from_env(wallet=wallet, on_event=lambda f: None,
                                    on_terminal=lambda c: None)
    print(f"# algo control over one WS connection ({wallet}, {coin}, mark={mark})\n")
    await client.start()

    exec_id = None
    try:
        # 1) START — launch a Glidemaker (passive, post-only) at limit_1.
        print(f"1) START  Glidemaker {coin} buy {size} @ {limit_1} (passive, post-only)")
        started = await _cmd(client, "start_algo", idempotency_key=_ik(),
                            symbol=coin, side="buy", total_size=size, algo="glidemaker",
                            params={"strategy": "passive", "limit_price": limit_1,
                                    "post_only": True}, reduce_only=False)
        if started:
            d = started.get("data") or {}
            exec_id = d.get("execution_id")
            # start_algo returns the launch acknowledgment: status='pending'. The
            # execution transitions to 'running' a moment later inside its first
            # loop (you'll see status='running' on the RESUME step below). There's
            # no Tier-2 command to re-read a single execution's status over the
            # socket, so we report the launch ack as-is rather than fake 'running'.
            print(f"   result={started.get('result')}  execution_id={exec_id}  "
                  f"algo={d.get('algo')}  status={d.get('status')} (launch ack)")
        if not exec_id:
            return
        await asyncio.sleep(2)

        # 2) PAUSE — patch requires a paused execution.
        print("\n2) PAUSE")
        paused = await _cmd(client, "pause_execution", idempotency_key=_ik(),
                            execution_id=exec_id)
        if paused:
            d = paused.get("data") or {}
            print(f"   result={paused.get('result')}  status={d.get('status')}")
        await asyncio.sleep(1)

        # 3) PATCH — retune limit_price while paused (Glidemaker-editable field).
        print(f"\n3) PATCH  limit_price -> {limit_2}")
        patched = await _cmd(client, "patch_execution_params", idempotency_key=_ik(),
                            execution_id=exec_id, params={"limit_price": limit_2})
        if patched:
            d = patched.get("data") or {}
            print(f"   result={patched.get('result')}  changes={d.get('changes')}")
        await asyncio.sleep(1)

        # 4) RESUME
        print("\n4) RESUME")
        resumed = await _cmd(client, "resume_execution", idempotency_key=_ik(),
                             execution_id=exec_id)
        if resumed:
            d = resumed.get("data") or {}
            print(f"   result={resumed.get('result')}  status={d.get('status')}")
        await asyncio.sleep(2)

    finally:
        # 5) CANCEL — stop the algo (with a reason). This is the cleanup.
        if exec_id:
            print("\n5) CANCEL")
            cancelled = await _cmd(client, "cancel_execution", idempotency_key=_ik(),
                                   execution_id=exec_id, reason="ws_algo_control_example")
            if cancelled:
                d = cancelled.get("data") or {}
                print(f"   result={cancelled.get('result')}  status={d.get('status')}")
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())

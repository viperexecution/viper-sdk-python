#!/usr/bin/env python3
"""
Sweep an algo's remaining size to market over the socket — complete_at_market.

complete_at_market tells a running execution to stop working passively and take
its REMAINING size at market immediately. Unlike the other examples (which rest
far from market and never fill), this one is AGGRESSIVE: it fills, leaving a real
position. The example closes that position at the end.

    start_algo            launch a Glidemaker near market (so it has size to sweep)
    complete_at_market    sweep the remaining size to market (fills)
    GET /v1/executions/id poll until it reaches a terminal state (sweep is async)
    close_position        flatten the position the sweep created (cleanup)

complete_at_market returns the PRE-sweep state (remaining_size, filled_before_sweep);
the actual market sweep runs asynchronously inside the algo loop, so we poll the
execution afterward to see it complete.

This example INTENTIONALLY FILLS a small market order (~$250) and then closes the
resulting position. Run it knowing it takes a real (briefly-held) position.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle        # optional
    export VIPER_WALLET=0x...              # one wallet per connection
    viper-examples ws-sweep

Tuning (optional env vars):
    VIPER_EXAMPLE_COIN=BTC                  # symbol (default BTC)
    VIPER_EXAMPLE_USD=250                   # algo notional (default 250)
"""
from __future__ import annotations

import os
import uuid
import asyncio

from viper import ViperWSClient, ViperRestClient

ORDER = 29
KIND = "ws"
SECTION = "Trading over WebSocket (Tier-3 writes)"
DESCRIPTION = "Sweep an algo's remaining size to market over the socket (Tier-3: complete_at_market — fills)."


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


async def main() -> None:
    wallet = os.environ.get("VIPER_WALLET", "").strip().lower()
    if not wallet:
        raise SystemExit("Set VIPER_WALLET to the wallet to trade on.")
    coin = os.environ.get("VIPER_EXAMPLE_COIN", "BTC")
    usd = float(os.environ.get("VIPER_EXAMPLE_USD", "250"))

    rest = ViperRestClient.from_env()
    await rest.__aenter__()
    try:
        px = await rest.price(coin)
        mark = float(px.get("mid") or px.get("mark") or px.get("price") or 0)
        if mark <= 0:
            raise SystemExit(f"could not read a mark for {coin}: {px!r}")
        size = max(round(usd / mark, 5), 0.00001)
        # Launch the Glidemaker just BELOW market so it rests with remaining size
        # to sweep (a passive limit right at/under mark — most stays unfilled until
        # we sweep it to market).
        near = round(mark * 0.999)

        client = ViperWSClient.from_env(wallet=wallet, on_event=lambda f: None,
                                        on_terminal=lambda c: None)
        print(f"# sweep-to-market over one WS connection ({wallet}, {coin}, mark={mark})\n")
        await client.start()

        exec_id = None
        try:
            # 1) START — a passive Glidemaker near market (rests with size remaining).
            print(f"1) START  Glidemaker {coin} buy {size} @ {near} (passive)")
            started = await _cmd(client, "start_algo", idempotency_key=_ik(),
                                symbol=coin, side="buy", total_size=size, algo="glidemaker",
                                params={"strategy": "passive", "limit_price": near,
                                        "post_only": True}, reduce_only=False)
            if started:
                exec_id = (started.get("data") or {}).get("execution_id")
                print(f"   execution_id={exec_id}")
            if not exec_id:
                return
            await asyncio.sleep(3)

            # 2) SWEEP — take the remaining size at market now. Returns pre-sweep state.
            print("\n2) COMPLETE AT MARKET (sweep remaining to market)")
            swept = await _cmd(client, "complete_at_market", idempotency_key=_ik(),
                              execution_id=exec_id, reason="ws_sweep_example")
            if swept:
                d = swept.get("data") or {}
                print(f"   result={swept.get('result')}  "
                      f"remaining_size={d.get('remaining_size')}  "
                      f"filled_before_sweep={d.get('filled_before_sweep')}")

            # 3) POLL — the sweep runs async; poll until terminal. Note the single-
            #    execution GET nests live status under `state` (not top-level).
            print("\n3) POLL execution until terminal (sweep is async)")
            for _ in range(6):
                await asyncio.sleep(1.0)
                ex = await rest.execution(exec_id)
                state = ex.get("state") or {}
                st = state.get("status")
                print(f"   status={st}  filled={state.get('filled_size')}/{state.get('total_size')}")
                if st in ("completed", "stopped", "error"):
                    break

        finally:
            # 4) CLEANUP — the sweep took a real position; close it.
            print("\n4) CLOSE POSITION (cleanup — the sweep filled)")
            try:
                if exec_id:
                    await _cmd(client, "cancel_execution", idempotency_key=_ik(),
                               execution_id=exec_id, reason="cleanup")
            except Exception:
                pass
            try:
                closed = await rest.close_position(symbol=coin)
                fs = closed.get("filled_size") if isinstance(closed, dict) else None
                print(f"   closed {fs if fs is not None else 'nothing'}")
            except Exception as e:
                print(f"   close note: {e}")
            await client.close()
    finally:
        await rest.__aexit__(None, None, None)


if __name__ == "__main__":
    asyncio.run(main())

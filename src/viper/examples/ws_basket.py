#!/usr/bin/env python3
"""
Launch a multi-leg basket over the socket — atomic compound order, Tier-3.

place_basket fires several algo legs as one atomic group. You get back a group_id
and a member list (one execution per leg, each with its own execution_id and algo
type), and you control the members individually after launch.

    place_basket   launch a 2-leg basket (pacemaker + glidemaker) atomically
    (inspect)      read the member list — group_id + per-leg execution_ids
    cancel_execution  stop each member (the basket's cleanup path over the socket)

Important — partial success: place_basket returns result='placed' even when only
SOME legs launched. ALWAYS check data.errors and members_launched vs total; a leg
can fail validation while others launch. (For all-or-nothing, the REST sibling
POST /v1/baskets/{group_id}/stop cascade-cancels; over the socket, cancel each
launched member as this example does.)

This fires REAL algos (~$250/leg, passive + far below market so they rest), then
cancels every member. Nothing should fill.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle        # optional
    export VIPER_WALLET=0x...              # one wallet per connection
    viper-examples ws-basket

Tuning (optional env vars):
    VIPER_EXAMPLE_COIN=BTC                  # symbol (default BTC)
    VIPER_EXAMPLE_USD=250                   # per-leg notional in USD (default 250)
"""
from __future__ import annotations

import os
import uuid
import asyncio

from viper import ViperWSClient, ViperRestClient

ORDER = 30
KIND = "ws"
SECTION = "Trading over WebSocket (Tier-3 writes)"
DESCRIPTION = "Launch a multi-leg basket over the socket (Tier-3: place_basket, atomic)."


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
        # all-failed baskets return an error frame with per-leg details
        for e in ((r.get("details") or {}).get("errors") or []):
            print(f"      leg {e.get('leg_index')}: {e.get('error_code')} "
                  f"{e.get('error_message')}")
        return None
    return r


async def main() -> None:
    wallet = os.environ.get("VIPER_WALLET", "").strip().lower()
    if not wallet:
        raise SystemExit("Set VIPER_WALLET to the wallet to trade on.")
    coin = os.environ.get("VIPER_EXAMPLE_COIN", "BTC")
    usd = float(os.environ.get("VIPER_EXAMPLE_USD", "250"))

    # One REST call for the mark, to size legs and pick a passive limit far below
    # market (so the legs rest rather than fill). The basket launch + cleanup are
    # all over the socket.
    async with ViperRestClient.from_env() as rest:
        px = await rest.price(coin)
        mark = float(px.get("mid") or px.get("mark") or px.get("price") or 0)
    if mark <= 0:
        raise SystemExit(f"could not read a mark for {coin}: {px!r}")
    size = max(round(usd / mark, 5), 0.00001)
    far = round(mark * 0.80)

    client = ViperWSClient.from_env(wallet=wallet, on_event=lambda f: None,
                                    on_terminal=lambda c: None)
    print(f"# basket over one WS connection ({wallet}, {coin}, mark={mark})\n")
    await client.start()

    members = []
    try:
        # 1) PLACE — a 2-leg basket: pacemaker (TWAP) + glidemaker (passive limit).
        print(f"1) PLACE BASKET  2 legs on {coin} (pacemaker buy + glidemaker buy)")
        placed = await _cmd(client, "place_basket", idempotency_key=_ik(),
                            symbol=coin, group_name="sdk example basket",
                            legs=[
                                {"algo": "pacemaker", "side": "buy", "total_size": size,
                                 "params": {"duration_seconds": 300, "urgency": "normal"}},
                                {"algo": "glidemaker", "side": "buy", "total_size": size,
                                 "limit_price": far,
                                 "params": {"strategy": "passive", "post_only": True}},
                            ])
        if placed:
            d = placed.get("data") or {}
            members = d.get("members") or []
            launched = d.get("members_launched")
            total = d.get("total")
            print(f"   group_id={d.get('group_id')}  side={d.get('side')}  "
                  f"launched={launched}/{total}")
            # Partial-success check — MUST inspect even on result='placed'.
            errs = d.get("errors")
            if errs:
                print(f"   PARTIAL: {len(errs)} leg(s) failed:")
                for e in errs:
                    print(f"     leg {e.get('leg_index')}: {e.get('error_code')} "
                          f"{e.get('error_message')}")

        # 2) INSPECT — the member list: one execution per leg, with reliable algo type.
        if members:
            print(f"\n2) MEMBERS ({len(members)})")
            for m in members:
                print(f"   leg {m.get('request_leg_index')}: {m.get('algo_type')} "
                      f"size={m.get('size')} status={m.get('status')}  "
                      f"id={m.get('execution_id')}")

    finally:
        # 3) CLEANUP — cancel each member execution over the socket.
        if members:
            print(f"\n3) CANCEL members ({len(members)})")
            for m in members:
                eid = m.get("execution_id")
                if not eid:
                    continue
                r = await _cmd(client, "cancel_execution", idempotency_key=_ik(),
                               execution_id=eid, reason="ws_basket_example")
                if r:
                    print(f"   {m.get('algo_type')} {eid}: {r.get('result')}")
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())

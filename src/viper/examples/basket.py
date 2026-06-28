#!/usr/bin/env python3
"""
Launch and manage a multi-leg basket over REST — atomic compound order.

A basket fires several algo legs as one atomic group (2-10 legs on one symbol). You
get back a group_id and a member list (one execution per leg), and you control the
whole group with basket-level commands — unlike cancelling members one by one.

    place_basket    launch a 2-leg basket (pacemaker + glidemaker) atomically
    basket          read the group detail (members, per-leg status)
    pause_basket    pause every member at once
    resume_basket   resume them
    stop_basket     cancel all members (the clean group-level teardown)

Partial success: place_basket returns even if only SOME legs launched — check the
members list and any errors.

Maps to REST endpoints:
    POST /v1/baskets
    GET  /v1/baskets/{group_id}
    POST /v1/baskets/{group_id}/pause | /resume | /stop

This fires REAL algos (~$250/leg, passive + far below market so they rest), then
stops the basket. Nothing should fill.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle
    export VIPER_WALLET=0x...
    viper-examples basket

Tuning (optional env vars):
    VIPER_EXAMPLE_COIN=BTC                  # symbol (default BTC)
    VIPER_EXAMPLE_USD=250                   # per-leg notional (default 250)
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperRestClient, ViperError


ORDER = 12
KIND = "rest"
SECTION = "Algorithms"
DESCRIPTION = "Launch + manage a multi-leg basket over REST (place/pause/resume/stop, group-level)."


async def main() -> None:
    coin = os.environ.get("VIPER_EXAMPLE_COIN", "BTC")
    usd = float(os.environ.get("VIPER_EXAMPLE_USD", "250"))

    try:
        async with ViperRestClient.from_env() as rest:
            px = await rest.price(coin)
            mark = px.get("mid") or px.get("mark") or px.get("price")
            if not isinstance(mark, (int, float)) or mark <= 0:
                raise SystemExit(f"could not read a mark for {coin}: {px!r}")
            size = max(round(usd / mark, 5), 0.00001)
            far = round(mark * 0.80)

            group_id = None
            try:
                # 1) PLACE — a 2-leg basket: pacemaker (TWAP) + glidemaker (passive).
                print(f"1) PLACE BASKET  2 legs on {coin} (pacemaker + glidemaker)")
                placed = await rest.place_basket(
                    symbol=coin, group_name="sdk rest basket",
                    legs=[
                        {"algo": "pacemaker", "side": "buy", "total_size": size,
                         "params": {"duration_seconds": 300, "urgency": "normal"}},
                        {"algo": "glidemaker", "side": "buy", "total_size": size,
                         "limit_price": far,
                         "params": {"strategy": "passive", "post_only": True}},
                    ])
                group_id = placed.get("group_id")
                members = placed.get("members") or []
                print(f"   group_id={group_id}  "
                      f"launched={placed.get('members_launched')}/{placed.get('total')}")
                for m in members:
                    print(f"     leg {m.get('request_leg_index')}: {m.get('algo_type')} "
                          f"size={m.get('size')} status={m.get('status')}")
                if placed.get("errors"):
                    print(f"   PARTIAL — errors: {placed.get('errors')}")
                if not group_id:
                    return
                await asyncio.sleep(2)

                # 2) DETAIL — read the group (richer per-member data than place).
                print("\n2) BASKET DETAIL")
                det = await rest.basket(group_id)
                dmembers = det.get("members") or det.get("legs") or []
                print(f"   status={det.get('status')}  members={len(dmembers)}")

                # 3) PAUSE the whole basket.
                print("\n3) PAUSE BASKET")
                r = await rest.pause_basket(group_id)
                print(f"   paused {r.get('paused_count')}/{r.get('total')}")
                await asyncio.sleep(2)

                # 4) RESUME.
                print("\n4) RESUME BASKET")
                r = await rest.resume_basket(group_id)
                print(f"   resumed {r.get('resumed_count')}/{r.get('total')}")
                await asyncio.sleep(2)

            finally:
                # 5) STOP — cancel all members at once (group-level teardown).
                if group_id:
                    print("\n5) STOP BASKET (cancel all members)")
                    r = await rest.stop_basket(group_id)
                    print(f"   stopped {r.get('stopped_count')}/{r.get('total')}")

    except ViperError as e:
        raise SystemExit(f"API error: {e}")


if __name__ == "__main__":
    asyncio.run(main())

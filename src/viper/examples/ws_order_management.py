#!/usr/bin/env python3
"""
Modify and bulk-cancel resting orders over the socket — Tier-3 order management.

Beyond place/cancel (see ws-order-control), two more order commands round out
order management over the socket:

    modify_order   change a resting order in place (new price/size)
    bulk_cancel    cancel several orders at once by id list

bulk_cancel has a partial-success contract: result='bulk_cancelled' even if some
ids failed — you MUST inspect data.errors[]. (Same shape as the REST sibling.)

Note: a modify returns a new order_id — the previous id is no longer live. So
after a modify, re-read open orders to get the current resting ids rather than
reusing the pre-modify id — which is what this example does before cancelling.

This rests two small post-only limits far below market (won't fill), modifies one,
then cancels whatever is actually resting. Nothing should fill.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle        # optional
    export VIPER_WALLET=0x...              # one wallet per connection
    viper-examples ws-order-management

Tuning (optional env vars):
    VIPER_EXAMPLE_COIN=BTC                  # symbol (default BTC)
    VIPER_EXAMPLE_USD=15                    # per-order notional (default 15)
"""
from __future__ import annotations

import os
import uuid
import asyncio

from viper import ViperWSClient, ViperRestClient

ORDER = 27
KIND = "ws"
SECTION = "Trading over WebSocket (Tier-3 writes)"
DESCRIPTION = "Modify + bulk-cancel resting orders over the socket (Tier-3: modify_order/bulk_cancel)."


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


async def _place(client, coin, side, size, price):
    r = await _cmd(client, "place_order", idempotency_key=_ik(),
                   symbol=coin, side=side, size=size, price=price,
                   order_type="limit", post_only=True, reduce_only=False)
    if r:
        return (r.get("data") or {}).get("order_ids", [None])[0]
    return None


async def main() -> None:
    wallet = os.environ.get("VIPER_WALLET", "").strip().lower()
    if not wallet:
        raise SystemExit("Set VIPER_WALLET to the wallet to trade on.")
    coin = os.environ.get("VIPER_EXAMPLE_COIN", "BTC")
    usd = float(os.environ.get("VIPER_EXAMPLE_USD", "15"))

    async with ViperRestClient.from_env() as rest:
        px = await rest.price(coin)
        mark = float(px.get("mid") or px.get("mark") or px.get("price") or 0)
    if mark <= 0:
        raise SystemExit(f"could not read a mark for {coin}: {px!r}")
    size = max(round(usd / mark, 5), 0.00001)
    p1 = round(mark * 0.80)
    p2 = round(mark * 0.78)
    p1_new = round(mark * 0.75)

    client = ViperWSClient.from_env(wallet=wallet, on_event=lambda f: None,
                                    on_terminal=lambda c: None)
    print(f"# order management over one WS connection ({wallet}, {coin}, mark={mark})\n")
    await client.start()

    ids = []
    try:
        # 1) PLACE two resting post-only buys far below market.
        print(f"1) PLACE two resting orders @ {p1} and {p2}")
        for p in (p1, p2):
            oid = await _place(client, coin, "buy", size, p)
            if oid:
                ids.append(oid)
                print(f"   order_id={oid} @ {p}")

        # 2) MODIFY the first order to a new price.
        #    a modify returns a new order_id — re-read open orders in step 3 to
        #    get the current resting ids rather than reusing this id.
        if ids:
            print(f"\n2) MODIFY order {ids[0]}  price -> {p1_new}")
            mod = await _cmd(client, "modify_order", idempotency_key=_ik(),
                            order_id=ids[0], symbol=coin, side="buy",
                            price=p1_new, size=size)
            if mod:
                d = mod.get("data") or {}
                print(f"   result={mod.get('result')}  {d}")
                print("   (a modify returns a new order_id — the live id differs; "
                      "we re-read open orders next)")

        # 3) BULK CANCEL — read the LIVE open orders and cancel exactly those, so
        #    the modify's replacement id is caught (not the stale pre-modify id).
        got = await _cmd(client, "get_orders")
        live = (got.get("data") or {}).get("items") or [] if got else []
        cancels = [{"symbol": o.get("symbol"), "order_id": o.get("order_id")}
                   for o in live if o.get("symbol") == coin]
        print(f"\n3) BULK CANCEL {len(cancels)} live order(s) on {coin}")
        if cancels:
            bc = await _cmd(client, "bulk_cancel", idempotency_key=_ik(), cancels=cancels)
            if bc:
                d = bc.get("data") or {}
                print(f"   result={bc.get('result')}  "
                      f"cancelled_count={d.get('cancelled_count')}  "
                      f"error_count={d.get('error_count')}")
                for e in (d.get("errors") or []):
                    print(f"   FAILED order {e.get('order_id')}: "
                          f"{e.get('reason')} {e.get('message')}")
        ids = []
    finally:
        # Safety net: re-read and cancel anything still resting on this coin.
        got = await _cmd(client, "get_orders")
        live = (got.get("data") or {}).get("items") or [] if got else []
        leftover = [{"symbol": o.get("symbol"), "order_id": o.get("order_id")}
                    for o in live if o.get("symbol") == coin]
        if leftover:
            await _cmd(client, "bulk_cancel", idempotency_key=_ik(), cancels=leftover)
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())

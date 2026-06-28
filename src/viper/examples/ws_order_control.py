#!/usr/bin/env python3
"""
Place and cancel orders over the socket — Tier-3 writes, no REST round-trip.

If your bot already holds a WebSocket connection, Tier-3 write commands let you
trade over that same socket instead of a separate authenticated REST call. This
shows the simplest write loop:

    place_order   rest a small limit order well away from market
    get_orders    confirm it's resting (Tier-2 read, same socket)
    cancel_order  cancel it by order_id

One REST call is used up front to read the live mark (so we can rest the order a
safe distance below it); the place / confirm / cancel all happen over the socket.

Mutating commands require an `idempotency_key` (the SDK's send_command adds the
client_correlation_id, but not the idempotency key — you supply it, exactly as
REST requires). The command result `data` mirrors the REST response.

This places a REAL order (a tiny limit far from market so it rests, then cancels
it). Nothing should fill.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle        # optional
    export VIPER_WALLET=0x...              # one wallet per connection
    viper-examples ws-order-control

Tuning (optional env vars):
    VIPER_EXAMPLE_COIN=BTC                  # symbol (default BTC)
    VIPER_EXAMPLE_USD=15                    # order notional in USD (default 15)
"""
from __future__ import annotations

import os
import uuid
import asyncio

from viper import ViperWSClient, ViperRestClient

ORDER = 26
KIND = "ws"
SECTION = "Trading over WebSocket (Tier-3 writes)"
DESCRIPTION = "Place + cancel an order over the socket (Tier-3: place_order/cancel_order)."


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
    usd = float(os.environ.get("VIPER_EXAMPLE_USD", "15"))

    # One REST call: get the live mark so we can rest the order safely far below it.
    # (Everything else — place, confirm, cancel — happens over the socket.)
    async with ViperRestClient.from_env() as rest:
        px = await rest.price(coin)
        mark = float(px.get("mid") or px.get("mark") or px.get("price") or 0)
    if mark <= 0:
        raise SystemExit(f"could not read a mark for {coin}: {px!r}")
    rest_price = round(mark * 0.80)          # 20% below market -> rests, won't fill
    size = max(round(usd / mark, 5), 0.00001)

    client = ViperWSClient.from_env(wallet=wallet, on_event=lambda f: None,
                                    on_terminal=lambda c: None)
    print(f"# order control over one WS connection ({wallet}, {coin}, mark={mark})\n")
    await client.start()

    order_id = None
    try:
        # 1) PLACE — a post-only limit 20% below market (rests, won't fill).
        print(f"1) PLACE  {coin} buy {size} @ {rest_price} (post-only, far from market)")
        placed = await _cmd(client, "place_order", idempotency_key=_ik(),
                            symbol=coin, side="buy", size=size, price=rest_price,
                            order_type="limit", post_only=True, reduce_only=False)
        if placed:
            d = placed.get("data") or {}
            order_id = (d.get("order_ids") or [None])[0]
            print(f"   result={placed.get('result')}  order_id={order_id}  "
                  f"status={d.get('status')}  resting_size={d.get('resting_size')}  "
                  f"builder_approved={d.get('builder_approved')}")

        # 2) CONFIRM — read open orders over the same socket (Tier-2).
        got = await _cmd(client, "get_orders")
        if got:
            items = (got.get("data") or {}).get("items") or []
            mine = [o for o in items if o.get("order_id") == order_id]
            print(f"\n2) CONFIRM  open orders={len(items)}  "
                  f"(ours resting: {'yes' if mine else 'no'})")

        # 3) CANCEL — by order_id.
        if order_id is not None:
            print(f"\n3) CANCEL  order_id={order_id}")
            cancelled = await _cmd(client, "cancel_order", idempotency_key=_ik(),
                                   symbol=coin, order_id=order_id)
            if cancelled:
                d = cancelled.get("data") or {}
                print(f"   result={cancelled.get('result')}  "
                      f"cancelled_at={d.get('cancelled_at')}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())

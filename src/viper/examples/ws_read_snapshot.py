#!/usr/bin/env python3
"""
Read account state over the socket you already have — no extra HTTP, no re-auth.

If your bot already holds a WebSocket connection (for streaming), Tier-2 read
commands let you pull a fresh snapshot over that same socket instead of making a
separate REST call + re-auth round-trip. Five reads are available; this fires four
of them and renders each:

    get_balance    full account balance (perp + spot + HIP-3 total)
    get_position   open positions
    get_orders     live resting orders
    get_fills      recent fills (cursor-paginated)

Each result's `data` is byte-identical to the matching REST endpoint's response,
so parsing logic written for REST works unchanged here.

Worth knowing: get_balance over Tier-2 returns the full total_account_value
(perp + spot + HIP-3) — unlike the account.state STREAM, whose balance_update
carries only the perp account value. So for total equity over a socket, this is
the command to use.

Multi-wallet: a WS connection is bound to ONE wallet, selected by VIPER_WALLET
(sent as the Viper-Wallet header at connect). If your handle has several linked
wallets, set VIPER_WALLET to the one you want and run again per wallet — there is
no all-wallets read on a single connection.

Read-only.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle        # optional
    export VIPER_WALLET=0x...              # the wallet to read
    viper-examples ws-read-snapshot
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperWSClient

ORDER = 23
KIND = "ws"
SECTION = "Streaming (WebSocket reads)"
DESCRIPTION = "Read account state over the socket (Tier-2: get_balance/position/orders/fills)."


async def _read(client, command: str, **fields):
    frame = {"command": command, **fields}
    r = await client.send_command(frame)
    if r is None:
        print(f"  {command}: (no response within timeout)")
        return None
    if r.get("result") == "error":
        print(f"  {command}: ERROR {r.get('code')}: {r.get('message')}")
        return None
    return r.get("data") or {}


async def main() -> None:
    wallet = os.environ.get("VIPER_WALLET", "").strip().lower()
    if not wallet:
        raise SystemExit("Set VIPER_WALLET to the wallet you want to read.")

    client = ViperWSClient.from_env(wallet=wallet, on_event=lambda f: None,
                                    on_terminal=lambda c: None)
    print(f"# reading account state for {wallet} over one WS connection\n")
    await client.start()
    try:
        # 1) BALANCE — full total (perp + spot + HIP-3), unlike the stream.
        bal = await _read(client, "get_balance")
        if bal is not None:
            print("1) BALANCE")
            xs = bal.get("exchange_specific") or {}
            mode = xs.get("account_mode")
            print(f"   total_account_value={bal.get('total_account_value')}  "
                  f"spot_total={bal.get('spot_total')}  mode={mode}")
            perp = bal.get("perp") or {}
            if perp:
                print(f"   perp: margin_used={perp.get('margin_used')}  "
                      f"notional_position={perp.get('notional_position')}")
            # spot tokens with non-zero value
            spot = [s for s in (bal.get("spot") or []) if (s.get("value_usdc") or 0)]
            if spot:
                toks = ", ".join(f"{s.get('asset')}=${s.get('value_usdc')}"
                                 for s in sorted(spot, key=lambda s: -(s.get('value_usdc') or 0)))
                print(f"   spot: {toks}")
            if xs.get("hip3_total"):
                print(f"   hip3_total=${xs.get('hip3_total')}")

        # 2) POSITIONS
        pos = await _read(client, "get_position")
        if pos is not None:
            items = pos.get("items") or []
            print(f"\n2) POSITIONS ({pos.get('count', len(items))})")
            for p in items:
                print(f"   {p.get('symbol')} {p.get('side')} size={p.get('size')} "
                      f"entry={p.get('entry_price')} uPnL={p.get('unrealized_pnl')}")
            if not items:
                print("   (flat)")

        # 3) OPEN ORDERS
        orders = await _read(client, "get_orders")
        if orders is not None:
            items = orders.get("items") or []
            print(f"\n3) OPEN ORDERS ({orders.get('count', len(items))})")
            for o in items[:10]:
                flags = []
                if o.get("reduce_only"):
                    flags.append("RO")
                if o.get("post_only"):
                    flags.append("PO")
                if o.get("is_trigger"):
                    flags.append("trigger")
                flag_s = f" [{','.join(flags)}]" if flags else ""
                print(f"   {o.get('symbol')} {o.get('side')} {o.get('size')} @ "
                      f"{o.get('price')}  {o.get('order_type')}{flag_s}  "
                      f"order_id={o.get('order_id')}")
            if not items:
                print("   (none resting)")

        # 4) RECENT FILLS — cursor-paginated; first page only here.
        fills = await _read(client, "get_fills", limit=5)
        if fills is not None:
            items = fills.get("items") or []
            print(f"\n4) RECENT FILLS (showing {len(items)}, has_more={fills.get('has_more')})")
            for f in items:
                print(f"   {f.get('symbol')} {f.get('side')} {f.get('size')} @ "
                      f"{f.get('price')}  fee={f.get('fee')}  "
                      f"{'maker' if f.get('is_maker') else 'taker'}")
            if fills.get("has_more"):
                print(f"   (next page: get_fills with cursor={fills.get('next_cursor')!r})")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())

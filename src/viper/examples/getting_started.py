#!/usr/bin/env python3
"""
Getting started with Viper — confirm your setup and see your account.

The fastest path from "I made API keys in the Viper UI" to "it works and here's my
account." Run this first. It uses only read-only REST calls and places no orders.

What it shows, in the order you'll want it:
    1. connections   which wallets are linked to your account (and their addresses)
                     — these are what you put in VIPER_WALLET elsewhere
    2. account type  unified vs standard mode
    3. balance       total equity, plus perp / spot breakdown
    4. positions     open positions
    5. open orders   resting orders
    6. recent fills  last few fills

Setup (keys are created in the Viper UI → API settings):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle        # your Viper handle
    export VIPER_WALLET=0x...              # optional; selects which linked wallet
                                           # the account reads report on (defaults
                                           # to your handle's primary wallet)
    # PowerShell:  $env:VIPER_API_KEY = "vk_..."   (etc.)

Run:
    viper-examples getting-started
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperRestClient, ViperError


ORDER = 0
KIND = "rest"
SECTION = "Getting Started"
DESCRIPTION = "Getting started: confirm setup and see your account (connections, balance, positions, orders, fills)."


def _hr(title: str) -> None:
    print(f"\n{'─' * 4} {title} {'─' * (40 - len(title))}")


async def main() -> None:
    handle = os.environ.get("VIPER_HANDLE", "")
    wallet = (os.environ.get("VIPER_WALLET", "") or "").strip().lower() or None

    try:
        async with ViperRestClient.from_env() as rest:
            # 1) CONNECTIONS — the wallets linked to your account.
            _hr("1) LINKED WALLETS (connections)")
            conns = await rest.connections()
            items = conns.get("connections") or []
            if not items:
                print("   (no connections found — link a wallet in the Viper UI first)")
            for c in items:
                star = "  *primary" if c.get("wallet_address", "").lower() == (wallet or "") else ""
                print(f"   {c.get('label') or '(no label)':16} {c.get('wallet_address')}  "
                      f"[{c.get('venue')}/{c.get('status')}]{star}")
            print("   ^ use any of these addresses as VIPER_WALLET")

            # The account reads below report on the wallet this client was built
            # with: VIPER_WALLET if you set it, otherwise your handle's primary
            # wallet. Selection is client-level (sent as the Viper-Wallet header) —
            # to report on a different linked wallet, set VIPER_WALLET to it and
            # re-run (same idea as the WS examples; one wallet per client).
            shown = wallet or "(handle default)"
            print(f"\n   Account reads below are for VIPER_WALLET={shown}")

            # 2) ACCOUNT TYPE — unified vs standard.
            _hr("2) ACCOUNT TYPE")
            at = await rest.account_type()
            print(f"   account_type={at.get('account_type')}  "
                  f"is_unified={at.get('is_unified')}  "
                  f"dex_abstraction={at.get('dex_abstraction_enabled')}")

            # 3) BALANCE — total equity + perp/spot.
            _hr("3) BALANCE")
            bal = await rest.balance()
            print(f"   total_account_value={bal.get('total_account_value')}  "
                  f"perp_total={bal.get('perp_total')}  spot_total={bal.get('spot_total')}")
            spot = [s for s in (bal.get('spot') or []) if (s.get('value_usdc') or 0)]
            if spot:
                toks = ", ".join(f"{s.get('asset')}=${s.get('value_usdc')}"
                                 for s in sorted(spot, key=lambda s: -(s.get('value_usdc') or 0))[:6])
                print(f"   spot: {toks}")

            # 4) POSITIONS.
            _hr("4) OPEN POSITIONS")
            pos = await rest.positions()
            plist = pos.get("positions") or pos.get("items") or []
            if not plist:
                print("   (flat — no open positions)")
            for p in plist:
                print(f"   {p.get('symbol')} {p.get('side', '')} size={p.get('size')} "
                      f"entry={p.get('entry_price')} uPnL={p.get('unrealized_pnl')}")

            # 5) OPEN ORDERS.
            _hr("5) OPEN ORDERS")
            od = await rest.orders()
            olist = od.get("items") or od.get("orders") or []
            if not olist:
                print("   (none resting)")
            for o in olist[:10]:
                print(f"   {o.get('symbol')} {o.get('side')} {o.get('size')} @ "
                      f"{o.get('price')}  {o.get('order_type')}  id={o.get('order_id')}")

            # 6) RECENT FILLS.
            _hr("6) RECENT FILLS")
            fl = await rest.fills(limit=5)
            flist = fl.get("items") or fl.get("fills") or []
            if not flist:
                print("   (no recent fills)")
            for f in flist[:5]:
                role = "maker" if f.get("is_maker") else "taker"
                print(f"   {f.get('symbol')} {f.get('side')} {f.get('size')} @ "
                      f"{f.get('price')}  fee={f.get('fee')}  {role}")

            _hr("DONE")
            print("   Setup confirmed. Next: try `viper-examples preview-algo` (dry-run),")
            print("   then `viper-examples start-glidemaker` for a live ~$250 test.")

    except ViperError as e:
        print(f"\nViper error: {e}")
        print("Check VIPER_API_KEY / VIPER_API_SECRET / VIPER_HANDLE are set correctly")
        print("(keys are created in the Viper UI under API settings).")


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""
One-shot snapshot of your account state.

"What's my account doing right now?" — pulls the core account reads concurrently
and prints a readable summary: account type, equity & margin, balances (incl.
per-token spot and HIP-3 collateral), builder status, fee tier, PnL, open
positions, and recent fills. The REST complement to stream_account_state.py,
which watches the same data live over WebSocket.

Read-only: no orders, no mutations. Needs API credentials.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle      # optional
    viper-examples account-snapshot

Tuning (optional env var):
    VIPER_EXAMPLE_FILLS=5        # how many recent fills to show (default 5)
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperRestClient, ViperError

ORDER = 15
SECTION = "Account & Market Data"
DESCRIPTION = "Account snapshot: equity, balances, builder, fees, PnL, positions, fills."


def _money(v, dp: int = 2) -> str:
    return f"${v:,.{dp}f}" if isinstance(v, (int, float)) else "n/a"


def _pct(v) -> str:
    return f"{v:+.3f}%" if isinstance(v, (int, float)) else "-"


def _ok(x):
    """gather(return_exceptions=True) yields either a result dict or an Exception."""
    return x if isinstance(x, dict) else None


async def main() -> None:
    fills_n = int(os.environ.get("VIPER_EXAMPLE_FILLS", "5"))

    rest = ViperRestClient.from_env()
    async with rest:
        # Lightweight account reads — fire concurrently. return_exceptions=True
        # so one slow/failed endpoint doesn't sink the whole snapshot.
        acct_type, state, balance, pnl, fee, builder = await asyncio.gather(
            rest.account_type(),
            rest.account_state(),
            rest.balance(),
            rest.pnl(),
            rest.fee_tier(),
            rest.builder_status(),
            return_exceptions=True,
        )

        # Fills are fetched on their own — NOT in the burst above. /v1/account/fills
        # proxies Hyperliquid's userFillsByTime, which can silently return an empty
        # list under concurrent load (it doesn't error). Fetch alone, and retry once
        # if an unexpected empty comes back.
        fills: object = None
        for attempt in range(2):
            try:
                fills = await rest.fills(limit=fills_n)
            except ViperError as e:
                fills = e
                break
            if isinstance(fills, dict) and fills.get("items"):
                break
            if attempt == 0:
                await asyncio.sleep(1.0)

    # ---- identity ----
    t = _ok(acct_type)
    if t:
        print(f"account_type={t.get('account_type')}  unified={t.get('is_unified')}  "
              f"dex_abstraction={t.get('dex_abstraction_enabled')}")

    # ---- equity & margin ----
    s = _ok(state)
    if s:
        print("\nEquity & margin")
        print(f"  equity={_money(s.get('equity'))}  "
              f"(perp {_money(s.get('perp_equity'))} / spot {_money(s.get('spot_equity'))})")
        print(f"  available_margin={_money(s.get('available_margin'))}  "
              f"margin_used={_money(s.get('margin_used'))}  "
              f"maint_margin={_money(s.get('maintenance_margin'))}  "
              f"leverage={s.get('account_leverage')}x")

    # ---- balances ----
    b = _ok(balance)
    if b:
        print(f"\nBalances  (wallet {b.get('wallet_address')}, {b.get('exchange')})")
        print(f"  total={_money(b.get('total_account_value'))}  "
              f"perp={_money(b.get('perp_total'))}  spot={_money(b.get('spot_total'))}")
        perp = b.get("perp") or {}
        if perp:
            print(f"  perp: account_value={_money(perp.get('account_value'))}  "
                  f"withdrawable={_money(perp.get('withdrawable'))}  "
                  f"margin_used={_money(perp.get('margin_used'))}  "
                  f"notional={_money(perp.get('notional_position'))}")
        # per-token spot balances, most valuable first
        spot = b.get("spot") or []
        priced = sorted([r for r in spot if isinstance(r, dict)],
                        key=lambda r: r.get("value_usdc") or 0, reverse=True)
        if priced:
            print("  spot tokens:")
            for r in priced:
                print(f"    {str(r.get('asset')):<8} "
                      f"total={r.get('total')}  available={r.get('available')}  "
                      f"value={_money(r.get('value_usdc'))}")
        # HIP-3 collateral per DEX (settlement differs per namespace)
        exspec = b.get("exchange_specific") or {}
        hip3 = exspec.get("hip3_dexes") or []
        if hip3:
            print(f"  HIP-3 collateral (total {_money(exspec.get('hip3_total'))}):")
            for d in hip3:
                print(f"    {str(d.get('dex')):<8} collateral={d.get('collateral')}  "
                      f"account_value={_money(d.get('account_value'))}  "
                      f"available={_money(d.get('available'))}  "
                      f"margin_used={_money(d.get('margin_used'))}  "
                      f"notional={_money(d.get('notional_position'))}")

    # ---- builder status ----
    bs = _ok(builder)
    if bs:
        print("\nBuilder status")
        print(f"  approved={bs.get('approved')}  "
              f"max_fee_bps={bs.get('max_fee_bps')}  max_fee={bs.get('max_fee')}")

    # ---- fee tier ----
    f = _ok(fee)
    if f:
        print("\nFee tier")
        print(f"  tier={f.get('tier')} ({f.get('tier_name')})  "
              f"builder_fee_bps={f.get('builder_fee_bps')}  "
              f"referral_fee_bps={f.get('referral_fee_bps')}  "
              f"volume_14d={_money(f.get('volume_14d'))}")

    # ---- PnL (pnl and volume are nested objects, not scalars) ----
    p = _ok(pnl)
    if p:
        pn = p.get("pnl") or {}
        vol = p.get("volume") or {}
        print("\nPnL")
        for span in ("day", "week", "month", "all_time"):
            if span in pn:
                print(f"  {span:<9} {_money(pn.get(span)):>14}  ({_pct(pn.get(span + '_pct'))})")
        if vol:
            print(f"  volume    day={_money(vol.get('day'))}  week={_money(vol.get('week'))}")

    # ---- open positions (curated; from account.state.positions) ----
    positions = (s or {}).get("positions") or []
    print(f"\nOpen positions: {len(positions)}")
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        size = pos.get("size")
        side = "long" if isinstance(size, (int, float)) and size > 0 else "short"
        liq = pos.get("liquidation_price")
        liq_s = f"{liq:,.2f}" if isinstance(liq, (int, float)) else "none"
        print(f"  {str(pos.get('symbol')):<12} {side:<5} size={size}  "
              f"entry={pos.get('entry_price')}  mark={pos.get('mark_price')}  "
              f"value={_money(pos.get('position_value'))}  "
              f"uPnL={_money(pos.get('unrealized_pnl'), 4)}  "
              f"lev={pos.get('leverage')}x  liq={liq_s}  "
              f"margin={_money(pos.get('margin_used'))}")

    # ---- recent fills (curated; fetched separately above) ----
    if isinstance(fills, Exception):
        print(f"\nRecent fills: unavailable ({type(fills).__name__}: {fills})")
    else:
        fl = fills if isinstance(fills, dict) else {}
        items = fl.get("items") or []
        print(f"\nRecent fills: {fl.get('count', len(items))} total, showing {min(len(items), fills_n)}")
        for fill in items[:fills_n]:
            if not isinstance(fill, dict):
                continue
            print(f"  {fill.get('timestamp')}  {str(fill.get('symbol')):<12} "
                  f"{str(fill.get('direction')):<12} size={fill.get('size')}  "
                  f"price={fill.get('price')}  maker={fill.get('is_maker')}  "
                  f"fee={_money(fill.get('fee'), 4)}  builder_fee={_money(fill.get('builder_fee'), 4)}  "
                  f"closed_pnl={_money(fill.get('closed_pnl'), 4)}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ViperError as e:
        raise SystemExit(f"API error: {e}")

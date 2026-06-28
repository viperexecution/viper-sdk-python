#!/usr/bin/env python3
"""
Place orders over REST: limit, market, and limit-with-attached-TP/SL.

The three order flows a bot actually uses, end to end:

  1. LIMIT          — a resting limit order (placed far from market so it rests),
                      then cancelled.
  2. MARKET         — a market order that fills, then the position is closed.
  3. LIMIT + TP/SL  — a limit parent with take_profit and stop_loss attached, which
                      produces the parent plus two reduce-only stop-market children
                      (the common "bracket" setup). Then cancelled.
  4. REDUCE-ONLY    — opens a small position, then rests a reduce-only limit above
                      it (a reduce-only order is rejected with no position to reduce).
                      Then cancelled and the position closed.

The /v1/order endpoint accepts exactly two order types — `limit` and `market`.
TP/SL are not separate order types; they attach to a limit/market parent via the
`take_profit` / `stop_loss` params and come back as `tp_order` / `sl_order`. The
same calls work for HIP-3 instruments — just pass a prefixed symbol (e.g.
"xyz:SP500"); no different order types are involved.

These place REAL orders on mainnet (default ~$15 notional). Set VIPER_EXAMPLE_SIZE
or VIPER_EXAMPLE_SYMBOL to change. There is no testnet.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle
    export VIPER_WALLET=0x...
    viper-examples place-order

Tuning (optional env vars):
    VIPER_EXAMPLE_SYMBOL=BTC      # instrument (default BTC; HIP-3 ok, e.g. xyz:SP500)
    VIPER_EXAMPLE_USD=15         # approx notional per order in USD (default 15)
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperRestClient, ViperError


ORDER = 2
SECTION = "Getting Started"
DESCRIPTION = "Place limit / market / limit+TP-SL orders over REST (live, tidies up)."


def _money(v) -> str:
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "n/a"


def _size_for(usd: float, mark: float, sz_decimals: int) -> float:
    """USD notional -> instrument size, floored to the instrument's lot."""
    raw = usd / mark
    q = 10 ** -sz_decimals
    return max(q, (raw // q) * q)


def _oids(resp: dict) -> list:
    return (resp or {}).get("order_ids") or []


async def main() -> None:
    symbol = os.environ.get("VIPER_EXAMPLE_SYMBOL", "BTC")
    usd = float(os.environ.get("VIPER_EXAMPLE_USD", "15"))

    rest = ViperRestClient.from_env()
    async with rest:
        # Sizing inputs from the instrument + live mark.
        inst = (await rest.instrument(symbol)).get("instrument") or {}
        sz_decimals = inst.get("sz_decimals")
        if not isinstance(sz_decimals, int):
            raise SystemExit(f"Could not read sz_decimals for {symbol}: {inst!r}")
        price = await rest.price(symbol)
        mark = price.get("ask") or price.get("mark_price") or price.get("price")
        if not isinstance(mark, (int, float)):
            raise SystemExit(f"Could not read a price for {symbol}: {price!r}")

        size = _size_for(usd, mark, sz_decimals)
        # A resting buy sits well below mark so it does NOT fill; round to whole.
        rest_px = round(mark * 0.80)
        print(f"{symbol} @ {_money(mark)} — size {size:g} (~{_money(size * mark)}); "
              f"resting limit @ {_money(rest_px)}\n")

        # 1) LIMIT — rests, then cancel.
        print("1) LIMIT")
        r1 = await rest.place_order(symbol=symbol, side="buy", size=size,
                                    order_type="limit", price=rest_px, post_only=True)
        print(f"   status={r1.get('status')}  order_ids={_oids(r1)}  "
              f"resting_size={r1.get('resting_size')}")
        for oid in _oids(r1):
            await rest.cancel_order(symbol=symbol, order_id=oid)
        print("   cancelled.\n")

        # 2) MARKET — fills, then close the position.
        print("2) MARKET")
        r2 = await rest.place_order(symbol=symbol, side="buy", size=size,
                                    order_type="market")
        print(f"   status={r2.get('status')}  filled_size={r2.get('filled_size')}  "
              f"avg_price={r2.get('avg_price')}")
        closed = await rest.close_position(symbol=symbol)
        if isinstance(closed, dict) and closed.get("filled_size") is not None:
            print(f"   closed {closed.get('filled_size'):g} @ "
                  f"{_money(closed.get('avg_price'))}\n")
        else:
            print(f"   closed position: {closed}\n")

        # 3) LIMIT + TP/SL — parent rests with two reduce-only children, then cancel.
        print("3) LIMIT + TP/SL")
        tp = round(mark * 1.20)
        sl = round(mark * 0.90)
        r3 = await rest.place_order(symbol=symbol, side="buy", size=size,
                                    order_type="limit", price=rest_px, post_only=True,
                                    take_profit=tp, stop_loss=sl)
        print(f"   status={r3.get('status')}  order_ids={_oids(r3)}")
        print(f"   tp_order={r3.get('tp_order')}")
        print(f"   sl_order={r3.get('sl_order')}")
        # Cancel the parent and both children (cancel-all on this symbol is simplest).
        await rest.cancel_all(symbol=symbol)
        print("   cancelled all on symbol.\n")

        # 4) REDUCE-ONLY LIMIT — a reduce-only order needs a position to reduce;
        #    the venue rejects it outright if there's nothing to reduce. So open a
        #    small long first, rest a reduce-only sell above market (it shows the RO
        #    flag and would scale the position out), then cancel and close.
        print("4) REDUCE-ONLY LIMIT")
        await rest.place_order(symbol=symbol, side="buy", size=size, order_type="market")
        ro_px = round(mark * 1.20)
        r4 = await rest.place_order(symbol=symbol, side="sell", size=size,
                                    order_type="limit", price=ro_px,
                                    post_only=True, reduce_only=True)
        print(f"   position opened; reduce-only sell @ {_money(ro_px)} -> "
              f"status={r4.get('status')}  order_ids={_oids(r4)}")
        for oid in _oids(r4):
            await rest.cancel_order(symbol=symbol, order_id=oid)
        closed = await rest.close_position(symbol=symbol)
        if isinstance(closed, dict) and closed.get("filled_size") is not None:
            print(f"   cancelled; closed {closed.get('filled_size'):g} @ "
                  f"{_money(closed.get('avg_price'))}")
        else:
            print(f"   cancelled; closed position: {closed}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ViperError as e:
        raise SystemExit(f"API error: {e}")

#!/usr/bin/env python3
"""
Indicator-derived TP/SL legs on an order — offset and at forms.

place-order §3 attaches TP/SL as two absolute prices. Legs can instead be
DERIVED, so the stop reflects actual volatility rather than a guessed
number. Two forms, demonstrated end to end:

  offset — mult x indicator, measured FROM the fill. An ATR-multiple stop:
           "1.8 ATRs below where I actually filled."
  at     — the trigger IS the indicator's value. "TP at the Bollinger
           upper" — no arithmetic, the level is the price.

And two timing modes:

  1. MARKET + legs   — the entry fills now, so both legs resolve INLINE:
                       the response carries live trigger orders plus a
                       `resolution` echo (indicator value, bar time,
                       anchor, resolved price) for each derived leg.
  2. RESTING + legs  — the entry rests, so there is nothing to anchor to
                       yet. Both legs come back `queued: true` and a
                       durable watch resolves them AT FILL, against the
                       real fill price. Watch it live with
                       `stream-tpsl-watch`.

Legs are independent: a leg that fails to resolve never blocks its
sibling (see `tpsl-troubleshooting` for what failure looks like).

Fires REAL orders on mainnet (~$15 notional default), tidies up fully.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle
    export VIPER_WALLET=0x...
    viper-examples tpsl-order-legs

Tuning (optional env vars):
    VIPER_EXAMPLE_SYMBOL=BTC
    VIPER_EXAMPLE_USD=15
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperRestClient, ViperError


ORDER = 13
SECTION = "TP/SL & Exits"
DESCRIPTION = "Attach indicator-derived TP/SL to an order (offset + at) — inline and queued-at-fill."


def _leg(r: dict | None) -> str:
    if not r:
        return "n/a"
    if r.get("queued"):
        return "queued=True (resolves at fill — see stream-tpsl-watch)"
    res = r.get("resolution") or {}
    out = f"oid={r.get('order_id')}  trigger={r.get('trigger_price')}"
    if res:
        out += (f"  [resolved: {res.get('indicator')}={res.get('indicator_value')}"
                f" @ bar {res.get('bar_time')} -> {res.get('resolved_price')}]")
    return out


async def main() -> None:
    symbol = os.environ.get("VIPER_EXAMPLE_SYMBOL", "BTC")
    usd = float(os.environ.get("VIPER_EXAMPLE_USD", "15"))

    rest = ViperRestClient.from_env()
    async with rest:
        inst = (await rest.instrument(symbol)).get("instrument") or {}
        sz_decimals = inst.get("sz_decimals")
        price = await rest.price(symbol)
        mark = price.get("ask") or price.get("mark_price") or price.get("price")
        if not isinstance(sz_decimals, int) or not isinstance(mark, (int, float)):
            raise SystemExit(f"Could not size {symbol}: {inst!r} / {price!r}")
        q = 10 ** -sz_decimals
        size = max(q, ((usd / mark) // q) * q)

        # Read the ATR yourself first — the same value the leg will use.
        ind = await rest.evaluate_indicators(
            symbol=symbol, interval="1h",
            indicators=[{"type": "atr", "params": {"period": 14}}])
        atr = ((ind.get("results") or [{}])[0].get("latest") or {}).get("atr")
        print(f"{symbol} @ ${mark:,.2f} — size {size:g} | ATR(14)@1h = {atr}\n")

        # 1) MARKET entry + derived legs -> both resolve inline.
        #    SL: 1.8 ATRs below the fill.  TP: at the Bollinger upper band.
        print("1) MARKET + derived legs (inline resolution)")
        r1 = await rest.place_order(
            symbol=symbol, side="buy", size=size, order_type="market",
            stop_loss={"offset": {"indicator": "atr", "mult": 1.8,
                                  "interval": "1h"}},
            take_profit={"at": {"indicator": "bollinger", "series": "upper",
                                "interval": "1h"}})
        print(f"   entry: filled_size={r1.get('filled_size')} "
              f"avg_price={r1.get('avg_price')}")
        print(f"   sl: {_leg(r1.get('sl_order'))}")
        print(f"   tp: {_leg(r1.get('tp_order'))}")
        await rest.cancel_all(symbol=symbol)
        await rest.close_position(symbol=symbol)
        print("   tidied: triggers cancelled, position closed.\n")

        # 2) RESTING entry + the same legs -> queued, resolved at fill.
        #    The anchor is the REAL fill price, not the price at submission.
        print("2) RESTING + derived legs (queued: true)")
        rest_px = round(mark * 0.80)
        r2 = await rest.place_order(
            symbol=symbol, side="buy", size=size, order_type="limit",
            price=rest_px, post_only=True,
            stop_loss={"offset": {"indicator": "atr", "mult": 1.8,
                                  "interval": "1h"}},
            take_profit={"at": {"indicator": "bollinger", "series": "upper",
                                "interval": "1h"}})
        print(f"   entry resting @ ${rest_px:,} -> order_ids={r2.get('order_ids')}")
        print(f"   sl: {_leg(r2.get('sl_order'))}")
        print(f"   tp: {_leg(r2.get('tp_order'))}")
        await rest.cancel_all(symbol=symbol)
        print("   tidied: resting entry cancelled (watch retires with it).")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ViperError as e:
        raise SystemExit(f"API error: {e}")

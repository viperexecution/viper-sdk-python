#!/usr/bin/env python3
"""
TP/SL troubleshooting — what refusals look like, and why legs are loud.

Derived legs validate hard, on purpose: a stop that silently lands on the
wrong side of price is worse than no stop, so the platform refuses rather
than guesses. Two refusals a bot dev will eventually meet, provoked
deliberately:

  1. WRONG-SIDED LEG (side-sanity). A take-profit for a LONG must sit
     ABOVE the entry — pointing it at the Bollinger LOWER band puts the
     level on the wrong side, and that leg is refused with the reason.
     Legs are independent: the valid stop-loss on the same order still
     places. One bad leg never costs you its sibling — but a refused
     leg is permanently absent, so read the per-leg result, not just
     the order status.

  2. NON-WHITELISTED INDICATOR (422). `at` and level-trails accept
     price-dimensioned indicators only — an RSI value of 60 is not a
     price, so {"at": {"indicator": "rsi"}} rejects the request with a
     machine-readable error envelope before anything is placed.

On the streaming side the same discipline appears as `leg_failed`
(with `stage` and `retrying`) and `leg_recovered` frames — see
`stream-tpsl-watch`.

Fires ONE real ~$15 market order on mainnet (case 1); tidies up.

Run (after `pip install viper-execution`):
    viper-examples tpsl-troubleshooting

Tuning (optional env vars):
    VIPER_EXAMPLE_SYMBOL=BTC
    VIPER_EXAMPLE_USD=15
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperRestClient, ViperError


ORDER = 17
SECTION = "TP/SL & Exits"
DESCRIPTION = "Provoke the two TP/SL refusals (wrong-sided leg, non-price indicator) and read them."


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

        # 1) Wrong-sided TP + valid SL on one order. The TP is refused
        #    (side-sanity); the SL places anyway — per-leg independence.
        print("1) WRONG-SIDED LEG — TP at the LOWER band, on a long")
        r1 = await rest.place_order(
            symbol=symbol, side="buy", size=size, order_type="market",
            take_profit={"at": {"indicator": "bollinger", "series": "lower",
                                "interval": "1h"}},
            stop_loss={"offset": {"indicator": "atr", "mult": 2.0,
                                  "interval": "1h"}})
        print(f"   order status: {r1.get('status')}")
        print(f"   tp_order: {r1.get('tp_order')}")
        print(f"   sl_order: {r1.get('sl_order')}")
        print("   -> the refused TP is PERMANENTLY absent; the SL is live.\n"
              "      Check per-leg results, not just the order status.\n")
        await rest.cancel_all(symbol=symbol)
        await rest.close_position(symbol=symbol)
        print("   tidied.\n")

        # 2) Non-price indicator in an `at` leg — rejected up front, 422,
        #    machine-readable envelope; nothing is placed.
        print("2) NON-WHITELISTED INDICATOR — at: rsi")
        try:
            await rest.place_order(
                symbol=symbol, side="buy", size=size, order_type="limit",
                price=round(mark * 0.80), post_only=True,
                take_profit={"at": {"indicator": "rsi", "interval": "1h"}})
            print("   unexpectedly accepted — tidying")
            await rest.cancel_all(symbol=symbol)
        except ViperError as e:
            payload = getattr(e, "payload", None) or {}
            err = payload.get("error") or {}
            print(f"   rejected: code={err.get('code')!r}")
            print(f"             message={err.get('message')!r}")
            print("   -> route on error.code (the contract); message is "
                  "display text.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ViperError as e:
        raise SystemExit(f"API error: {e}")

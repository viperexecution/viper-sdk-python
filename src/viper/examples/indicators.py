#!/usr/bin/env python3
"""
Server-side candles + technical indicators over REST.

Fetches recent closed candles (GET /v1/candles/{symbol}), then evaluates a
batch of indicators — ATR, Bollinger, EMA, RSI — in one call
(POST /v1/indicators/evaluate). The same fourteen indicator types the
dashboard chart draws, computed server-side, so your bot never reimplements
the math: values bind to the last CLOSED bar (stamped in `bar_time`), and the
in-progress bar only appears when you explicitly opt in — repainting is always
your choice, never an accident. Every response carries the cache honesty
fields (`cached`, `age_seconds`).

Read-only. Needs only API credentials.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_EXAMPLE_SYMBOL=BTC       # which symbol (default BTC)
    export VIPER_EXAMPLE_INTERVAL=1h      # 1m 5m 15m 1h 4h 1d (default 1h)
    viper-examples indicators
"""
from __future__ import annotations

import os
import asyncio
from datetime import datetime, timezone

from viper import ViperRestClient, ViperError

ORDER = 22
KIND = "rest"
SECTION = "Account & Market Data"
DESCRIPTION = "Candles + batch server-side indicators (ATR/Bollinger/EMA/RSI) on the last closed bar."


def _utc(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


async def main() -> None:
    symbol = os.environ.get("VIPER_EXAMPLE_SYMBOL", "BTC")
    interval = os.environ.get("VIPER_EXAMPLE_INTERVAL", "1h")

    rest = ViperRestClient.from_env()
    async with rest:
        # --- candles: closed bars only; the newest closed bar is candles[-1]
        book = await rest.candles(symbol, interval=interval, limit=5)
        print(f"{symbol} {interval} — last {book['count']} closed bars "
              f"(cached={book['cached']}, age={book['age_seconds']}s)")
        for bar in book["candles"]:
            print(f"  {_utc(bar['t'])}  o={bar['o']} h={bar['h']} "
                  f"l={bar['l']} c={bar['c']} v={bar['v']}")

        # --- batch evaluation on the last closed bar
        res = await rest.evaluate_indicators(
            symbol=symbol, interval=interval,
            indicators=[
                {"type": "atr", "params": {"period": 14}},
                {"type": "bollinger", "params": {"period": 20, "mult": 2}},
                {"type": "ema", "params": {"period": 50, "source": "hl2"}},
                {"type": "rsi", "params": {"period": 14}},
            ],
        )
        print(f"\nindicators @ closed bar {_utc(res['bar_time'])}:")
        for item in res["results"]:
            vals = ", ".join(
                f"{k}={v if v is not None else 'warming up'}"
                for k, v in item["latest"].items()
            )
            print(f"  {item['type']:<10} {vals}")

        # --- explicit opt-in: the forming (repainting) bar instead
        live = await rest.evaluate_indicators(
            symbol=symbol, interval=interval,
            indicators=[{"type": "rsi", "params": {"period": 14}}],
            include_forming=True,
        )
        rsi = live["results"][0]["latest"].get("rsi")
        print(f"\nforming bar {_utc(live['bar_time'])} (values change until "
              f"close): rsi={rsi}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ViperError as e:
        raise SystemExit(f"API error: {e}")

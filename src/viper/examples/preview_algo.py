#!/usr/bin/env python3
"""
Dry-run every execution algo with /v1/execute/preview — no orders placed.

"What would each algo do?" — preview sizes, prices, and validates an execution
WITHOUT launching it. No order, no idempotency, no throttle. It's the safe step
before `execute`: you see the rounded size, reference price, notional, any
warnings/errors, and the algo-specific plan (trajectory, levels, first order).

This previews all six algos on one symbol with sensible default params, so you
can see each algo's request and response shape at a glance. The response is a
common envelope (same fields for every algo) plus algo-specific fields on top.

Read-only: nothing is launched. Needs API credentials.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    viper-examples preview-algo

Tuning (optional env vars):
    VIPER_EXAMPLE_SYMBOL=BTC     # symbol to preview against (default BTC)
    VIPER_EXAMPLE_SIZE=0.005     # total size (default 0.005)
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperRestClient, ViperError

ORDER = 1
SECTION = "Getting Started"
DESCRIPTION = "Dry-run all six algos via /v1/execute/preview (no orders placed)."

# The common envelope every preview returns (per the public spec). Everything
# else in the response is algo-specific and printed below it.
_ENVELOPE = (
    "algo", "symbol", "side", "total_size", "rounded_size",
    "reference_price", "notional_usd", "preview_accuracy",
    "warnings", "notices", "errors",
)

# Default params per algo, with a sensible side. Price-relative params
# (limit_price / trigger_price) are filled from the live mark at runtime so they
# never go stale. These are example defaults — each algo accepts more (see docs).
def _plans(mark: float):
    return [
        ("pacemaker",  "buy",  {"duration_seconds": 600, "urgency": "normal"}),
        ("flowscale",  "buy",  {"range_from_pct": 0.5, "range_to_pct": 2.0, "num_clips": 5}),
        ("flowband",   "buy",  {"range_from_pct": 0.5, "range_to_pct": 2.0}),
        ("glidemaker", "buy",  {"strategy": "passive", "limit_price": round(mark * 0.80)}),
        ("ghostsweep", "buy",  {"strategy": "neutral", "trigger_price": round(mark * 0.98)}),
        ("smart_exit", "sell", {"strategy": "aggressive", "limit_price": round(mark * 1.20)}),
    ]


def _money(v) -> str:
    return f"${v:,.2f}" if isinstance(v, (int, float)) else "n/a"


def _msg(entry) -> str:
    """A warning/notice/error entry can be a bare string or a {message: ...}
    object — pull the readable text either way."""
    if isinstance(entry, dict):
        return str(entry.get("message") or entry.get("msg") or entry)
    return str(entry)


def _render(algo: str, side: str, resp: dict) -> None:
    print(f"\n=== {algo}  {side.upper()} ===")
    print(f"  rounded_size={resp.get('rounded_size')}  "
          f"reference_price={_money(resp.get('reference_price'))}  "
          f"notional={_money(resp.get('notional_usd'))}  "
          f"accuracy={resp.get('preview_accuracy')}")
    # errors/warnings/notices, deduped — the server sometimes echoes the same
    # message in more than one bucket (e.g. smart_exit's no-position note).
    seen: set[str] = set()
    for bucket in ("errors", "warnings", "notices"):
        for entry in resp.get(bucket) or []:
            text = _msg(entry)
            if text in seen:
                continue
            seen.add(text)
            print(f"  {bucket[:-1]}: {text}")
    extra = {k: resp.get(k) for k in resp if k not in _ENVELOPE}
    for k in sorted(extra):
        v = extra[k]
        # Long arrays (e.g. pacemaker trajectory, flowscale levels) print as a
        # count so one algo doesn't flood the screen; short ones print in full.
        if isinstance(v, list) and len(v) > 6:
            print(f"  {k}: [{len(v)} items] {v[0]} ... {v[-1]}")
        else:
            print(f"  {k}: {v}")


async def main() -> None:
    symbol = os.environ.get("VIPER_EXAMPLE_SYMBOL", "BTC")
    total_size = float(os.environ.get("VIPER_EXAMPLE_SIZE", "0.005"))

    rest = ViperRestClient.from_env()
    async with rest:
        price = await rest.price(symbol)
        mark = price.get("ask") or price.get("mark_price") or price.get("price")
        if not isinstance(mark, (int, float)):
            raise SystemExit(f"Could not read a price for {symbol}: {price}")

        print(f"Preview — {symbol} @ {_money(mark)}, size {total_size} (no orders placed)")
        for algo, side, params in _plans(mark):
            try:
                resp = await rest.preview(algo=algo, symbol=symbol, side=side,
                                          total_size=total_size, params=params)
            except ViperError as e:
                print(f"\n=== {algo}  {side.upper()} ===")
                print(f"  preview rejected ({type(e).__name__}): {e}")
                continue
            _render(algo, side, resp)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ViperError as e:
        raise SystemExit(f"API error: {e}")

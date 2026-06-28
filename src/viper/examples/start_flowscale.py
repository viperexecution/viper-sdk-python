#!/usr/bin/env python3
"""
Fire FlowScale (scaled ladder) on BTC over REST — live mainnet.

FlowScale lays a scaled ladder of clips across a percentage band relative to the
market. This example uses a 0.5%-2% band with the minimum 5 clips; for a BUY the
ladder rests below market and won't fully fill in the short observe window, then
gets cancelled. Places real orders (~$250 total). There is no testnet.

Env: VIPER_API_KEY, VIPER_API_SECRET, VIPER_HANDLE, VIPER_WALLET (+ optional
VIPER_EXAMPLE_USD / _OBSERVE_S / _NO_CANCEL). Run:
    viper-examples start-flowscale
"""
import asyncio

from viper.examples._algo_rest_common import run_buy_algo

ORDER = 7
SECTION = "Algorithms"
DESCRIPTION = "Fire FlowScale (scaled ladder) on BTC over REST — live ~$250, auto-cancels."


def _params(mark: float) -> dict:
    # scaled ladder 0.5%-2% out, 5 clips (the minimum). pct band -> no absolute price.
    return {"range_from_pct": 0.5, "range_to_pct": 2.0, "num_clips": 5}


async def main():
    await run_buy_algo(algo="flowscale", build_params=_params, label="FlowScale")


if __name__ == "__main__":
    asyncio.run(main())

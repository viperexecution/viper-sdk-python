#!/usr/bin/env python3
"""
Fire GhostSweep (hidden stop/take) on BTC over REST — live mainnet.

GhostSweep arms a hidden sweep at a trigger price. For a BUY it activates when
ask <= trigger_price, so this example sets trigger 5% BELOW mark: the sweep arms
as a hidden buy-stop on a dip and does NOT fire within the observe window, then
gets cancelled. Places a real order (~$250). There is no testnet.

Env: VIPER_API_KEY, VIPER_API_SECRET, VIPER_HANDLE, VIPER_WALLET (+ optional
VIPER_EXAMPLE_USD / _OBSERVE_S / _NO_CANCEL). Run:
    viper-examples start-ghostsweep
"""
import asyncio

from viper.examples._algo_rest_common import run_buy_algo

ORDER = 6
SECTION = "Algorithms"
DESCRIPTION = "Fire GhostSweep (hidden stop) on BTC over REST — live ~$250, auto-cancels."


def _params(mark: float) -> dict:
    # BUY sweep arms when ask <= trigger_price; 5% below mark -> arms, won't fire now.
    return {"strategy": "neutral", "trigger_price": round(mark * 0.95)}


async def main():
    await run_buy_algo(algo="ghostsweep", build_params=_params, label="GhostSweep")


if __name__ == "__main__":
    asyncio.run(main())

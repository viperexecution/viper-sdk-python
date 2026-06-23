#!/usr/bin/env python3
"""
Fire FlowBand (floating stealth scale) on BTC over REST — live mainnet.

FlowBand floats a stealth scale of levels across a percentage band. This example
uses a 0.5%-2% band (the required range params); for a BUY the levels rest below
market and won't fully fill in the short observe window, then get cancelled.
Places real orders (~$250 total). There is no testnet.

Env: VIPER_API_KEY, VIPER_API_SECRET, VIPER_HANDLE, VIPER_WALLET (+ optional
VIPER_EXAMPLE_USD / _OBSERVE_S / _NO_CANCEL). Run:
    viper-examples start-flowband
"""
import asyncio

from viper.examples._algo_rest_common import run_buy_algo

ORDER = 7
DESCRIPTION = "Fire FlowBand (floating stealth scale) on BTC over REST — live ~$250, auto-cancels."


def _params(mark: float) -> dict:
    # required percentage band; num_levels/flow_band/bias take server defaults.
    return {"range_from_pct": 0.5, "range_to_pct": 2.0}


async def main():
    await run_buy_algo(algo="flowband", build_params=_params, label="FlowBand")


if __name__ == "__main__":
    asyncio.run(main())

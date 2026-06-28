#!/usr/bin/env python3
"""
Fire Glidemaker on BTC — live mainnet execution.

Glidemaker is passive limit execution: it rests an order and works it without
crossing the spread, so within a short observe window it may or may not fill.
This example sizes to a USD notional (default $250) off the live mark, discloses
the order with a Ctrl-C abort window, fires once over the WebSocket command
surface, observes briefly, then cancels.

Places a REAL order on mainnet (no testnet). Env + knobs documented in
viper.examples._algo_common.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle
    export VIPER_WALLET=0x...
    viper-examples start-glidemaker
"""
import asyncio

from viper.examples._algo_common import run_algo

ORDER = 3
SECTION = "Algorithms"
DESCRIPTION = "Fire Glidemaker (passive limit) on BTC — live ~$250, auto-cancels."


async def main():
    await run_algo(
        algo="glidemaker",
        side="buy",
        params={"strategy": "passive", "post_only": True},
        label="Glidemaker",
        client_correlation_id="gm-example-1",
    )


if __name__ == "__main__":
    asyncio.run(main())

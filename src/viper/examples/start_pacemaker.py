#!/usr/bin/env python3
"""
Fire Pacemaker on BTC — live mainnet execution.

Pacemaker is TWAP: it works the order over a duration (minimum 300s), so within
a short observe window it will typically have placed only its first slice — it
may or may not have filled yet. This example sizes to a USD notional
(default $250) off the live mark, discloses the order with a Ctrl-C abort
window, fires once over the WebSocket command surface, observes briefly, then
cancels the remainder.

Places a REAL order on mainnet (no testnet). Env + knobs documented in
viper.examples._algo_common.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle
    export VIPER_WALLET=0x...
    viper-examples start-pacemaker
"""
import asyncio

from viper.examples._algo_common import run_algo

ORDER = 3
DESCRIPTION = "Fire Pacemaker (TWAP) on BTC — live ~$250, auto-cancels."


async def main():
    await run_algo(
        algo="pacemaker",
        side="buy",
        params={"duration_seconds": 300, "urgency": "normal"},
        label="Pacemaker",
        client_correlation_id="pm-example-1",
    )


if __name__ == "__main__":
    asyncio.run(main())

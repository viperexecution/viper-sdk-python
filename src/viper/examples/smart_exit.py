#!/usr/bin/env python3
"""
Fire Smart Exit (conditional reduce-only exit) on an existing BTC long — REST,
live mainnet.

Smart Exit is a reduce-only stop: for a SELL exit it fires when ask < limit_price.
This example finds your existing BTC long, arms a sell-stop 5% BELOW mark (so it
rests as protection and does NOT fire within the observe window), then cancels.
reduce_only is forced by the algo — the request does not declare it.

Smart Exit exits a position; it does not open one. If you have no BTC long, the
example tells you and exits without firing. Open a small long (~$250) first, then
re-run. There is no testnet.

Env: VIPER_API_KEY, VIPER_API_SECRET, VIPER_HANDLE, VIPER_WALLET (+ optional
VIPER_EXAMPLE_OBSERVE_S / _NO_CANCEL). Run:
    viper-examples smart-exit
"""
import uuid
import asyncio

from viper import ViperRestClient, ViperError
from viper.examples._algo_rest_common import (
    require_env, instrument_mark, poll_and_cancel, ABORT_S, SYMBOL,
)

ORDER = 8
DESCRIPTION = "Arm Smart Exit (reduce-only stop) on an existing BTC long over REST — live, auto-cancels."


def _find_long(positions: dict):
    """Return the signed size of the BTC long, or None. size>0 is long."""
    for p in (positions or {}).get("items", []):
        if str(p.get("symbol", "")).upper() == SYMBOL:
            size = float(p.get("size") or 0)
            if size > 0:
                return size
    return None


async def main():
    require_env()
    rest = ViperRestClient.from_env()
    async with rest:
        long_size = _find_long(await rest.positions())
        if not long_size:
            print(f"# Smart Exit exits an existing {SYMBOL} long; none found.")
            print(f"# Open a small {SYMBOL} long (~$250) first, then re-run.")
            return

        mark, _, _ = await instrument_mark(rest)
        # SELL exit fires when ask < limit_price; 5% below mark -> arms as a
        # stop, won't fire now.
        limit_price = round(mark * 0.95)
        params = {"strategy": "neutral", "limit_price": limit_price}

        print(f"# Smart Exit: LIVE reduce-only sell-stop on {long_size} {SYMBOL} "
              f"long — limit {limit_price} (~5% below mark {mark}).")
        print(f"#   params={params}")
        print(f"# Ctrl-C within {ABORT_S:.0f}s to abort (nothing sent yet)...")
        try:
            await asyncio.sleep(ABORT_S)
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\n# aborted — nothing was sent.")
            return

        idem = uuid.uuid4().hex
        try:
            # No reduce_only kwarg: Smart Exit forces it at the algo layer, and
            # the request schema rejects a declared reduce_only.
            res = await rest.execute(algo="smart_exit", symbol=SYMBOL, side="sell",
                                     total_size=long_size, params=params,
                                     idempotency_key=idem)
        except ViperError as e:
            print(f"# execute failed: {type(e).__name__} code={e.code} status={e.status}")
            return

        exec_id = res.get("execution_id") or res.get("id")
        print(f"# Smart Exit: launched execution_id={exec_id} status={res.get('status')}")
        if exec_id:
            await poll_and_cancel(rest, exec_id, "Smart Exit")


if __name__ == "__main__":
    asyncio.run(main())

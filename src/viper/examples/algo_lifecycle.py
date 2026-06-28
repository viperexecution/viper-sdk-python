#!/usr/bin/env python3
"""
Drive an algo through its whole life over REST — launch, pause, retune, resume, cancel.

The REST counterpart to ws-algo-control: take one execution from launch to finish
using plain REST calls (no socket). This is the pattern for a bot that manages its
algos request-by-request.

    execute          launch a Glidemaker (passive limit)
    pause            pause it (required before editing params)
    update_params    retune it while paused (limit_price)
    resume           resume
    cancel_execution stop it

Each step maps to one REST endpoint:
    POST  /v1/execute
    POST  /v1/executions/{id}/pause
    PATCH /v1/executions/{id}/params
    POST  /v1/executions/{id}/resume
    POST  /v1/executions/{id}/cancel

This fires a REAL ~$250 Glidemaker (passive, far below market so it rests), then
cancels it. Nothing should fill.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle
    export VIPER_WALLET=0x...
    viper-examples algo-lifecycle

Tuning (optional env vars):
    VIPER_EXAMPLE_COIN=BTC                  # symbol (default BTC)
    VIPER_EXAMPLE_USD=250                   # algo notional (default 250)
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperRestClient, ViperError


ORDER = 10
KIND = "rest"
SECTION = "Algorithms"
DESCRIPTION = "Drive an algo through its lifecycle over REST (execute/pause/update_params/resume/cancel)."


async def main() -> None:
    coin = os.environ.get("VIPER_EXAMPLE_COIN", "BTC")
    usd = float(os.environ.get("VIPER_EXAMPLE_USD", "250"))

    try:
        async with ViperRestClient.from_env() as rest:
            px = await rest.price(coin)
            mark = px.get("mid") or px.get("mark") or px.get("price")
            if not isinstance(mark, (int, float)) or mark <= 0:
                raise SystemExit(f"could not read a mark for {coin}: {px!r}")
            size = max(round(usd / mark, 5), 0.00001)
            limit_1 = round(mark * 0.80)         # initial passive limit (rests)
            limit_2 = round(mark * 0.78)         # retuned limit (still rests)

            print(f"# algo lifecycle over REST ({coin}, mark={mark})\n")

            exec_id = None
            try:
                # 1) LAUNCH a passive Glidemaker.
                print(f"1) EXECUTE  Glidemaker {coin} buy {size} @ {limit_1} (passive)")
                started = await rest.execute(algo="glidemaker", symbol=coin, side="buy",
                                            total_size=size,
                                            params={"strategy": "passive",
                                                    "limit_price": limit_1,
                                                    "post_only": True})
                exec_id = started.get("execution_id")
                print(f"   execution_id={exec_id}  status={started.get('status')}")
                if not exec_id:
                    return
                await asyncio.sleep(2)

                # 2) PAUSE (required before editing params).
                print("\n2) PAUSE")
                r = await rest.pause(exec_id)
                print(f"   action={r.get('action')}  status={r.get('status')}")
                await asyncio.sleep(1)

                # 3) UPDATE PARAMS — retune limit_price while paused.
                print(f"\n3) UPDATE_PARAMS  limit_price -> {limit_2}")
                r = await rest.update_params(exec_id, params={"limit_price": limit_2})
                print(f"   changes={r.get('changes')}")
                await asyncio.sleep(1)

                # 4) RESUME.
                print("\n4) RESUME")
                r = await rest.resume(exec_id)
                print(f"   action={r.get('action')}  status={r.get('status')}")
                await asyncio.sleep(2)

            finally:
                # 5) CANCEL — stop the algo (cleanup).
                if exec_id:
                    print("\n5) CANCEL")
                    r = await rest.cancel_execution(exec_id)
                    print(f"   action={r.get('action')}  status={r.get('status')}")

    except ViperError as e:
        raise SystemExit(f"API error: {e}")


if __name__ == "__main__":
    asyncio.run(main())

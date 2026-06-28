#!/usr/bin/env python3
"""
Drive a running execution's state, narrating each action (a driver for the
`stream-execution` example — run this in one terminal, stream in another).

Like `monitor-actions`, this performs the actions; pair it with `stream-execution`
to watch the matching execution.state frames land live. It:

  launches  a passive Glidemaker, prints its execution_id, and PAUSES so you can
            start the stream on that id
  pauses / resumes  the execution (you'll see status=paused / status=running on
            the stream)
  cancels   it (status=stopped, then execution_ended)

This is where stream-execution stops looking quiet: a passive algo on its own just
rests, but driving it here produces the state transitions on the stream.

Two-terminal flow:
    # terminal 1 — start this; copy the printed execution id; press Enter when streaming
    viper-examples algo-actions
    # terminal 2 — stream that execution
    export VIPER_WALLET=0x...
    export VIPER_EXAMPLE_EXECUTION_ID=exec_...   # the id terminal 1 printed
    viper-examples stream-execution

Fires a REAL ~$250 Glidemaker (passive, far below market so it rests); cancelled
after. Nothing should fill.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle
    export VIPER_WALLET=0x...
    viper-examples algo-actions

Tuning (optional env vars):
    VIPER_EXAMPLE_COIN=BTC        # coin (default BTC)
    VIPER_EXAMPLE_USD=250         # algo notional (default 250)
    VIPER_EXAMPLE_PAUSE=1         # 1 to wait for Enter before driving (default 1)
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperRestClient, ViperError


ORDER = 11
KIND = "rest"
SECTION = "Algorithms"
DESCRIPTION = "Drive a running execution (pause/resume/cancel) — pair with stream-execution."


async def main() -> None:
    coin = os.environ.get("VIPER_EXAMPLE_COIN", "BTC")
    usd = float(os.environ.get("VIPER_EXAMPLE_USD", "250"))
    want_pause = os.environ.get("VIPER_EXAMPLE_PAUSE", "1") == "1"

    try:
        async with ViperRestClient.from_env() as rest:
            px = await rest.price(coin)
            mark = px.get("mid") or px.get("mark") or px.get("price")
            if not isinstance(mark, (int, float)) or mark <= 0:
                raise SystemExit(f"could not read a mark for {coin}: {px!r}")
            size = max(round(usd / mark, 5), 0.00001)
            limit = round(mark * 0.80)

            exec_id = None
            try:
                # LAUNCH
                started = await rest.execute(algo="glidemaker", symbol=coin, side="buy",
                                            total_size=size,
                                            params={"strategy": "passive",
                                                    "limit_price": limit,
                                                    "post_only": True})
                exec_id = started.get("execution_id")
                print(f"launched execution: {exec_id}")
                if not exec_id:
                    return

                # Pause for the operator to start the stream on this id.
                if want_pause:
                    print("\n  >>> In another terminal, stream this execution:")
                    print(f"        export VIPER_EXAMPLE_EXECUTION_ID={exec_id}")
                    print("        viper-examples stream-execution")
                    try:
                        input("\n  Press Enter here once the stream is running... ")
                    except EOFError:
                        await asyncio.sleep(3)
                else:
                    await asyncio.sleep(3)

                # PAUSE — watch status=paused appear on the stream.
                print("\n1) PAUSE")
                r = await rest.pause(exec_id)
                print(f"   action={r.get('action')}  status={r.get('status')}")
                await asyncio.sleep(4)

                # RESUME — watch status=running.
                print("\n2) RESUME")
                r = await rest.resume(exec_id)
                print(f"   action={r.get('action')}  status={r.get('status')}")
                await asyncio.sleep(4)

            finally:
                # CANCEL — watch status=stopped + execution_ended on the stream.
                if exec_id:
                    print("\n3) CANCEL")
                    r = await rest.cancel_execution(exec_id)
                    print(f"   action={r.get('action')}  status={r.get('status')}")

    except ViperError as e:
        raise SystemExit(f"API error: {e}")


if __name__ == "__main__":
    asyncio.run(main())

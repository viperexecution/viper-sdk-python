"""
Shared REST launch flow for the price-level algo examples (GhostSweep,
FlowScale, FlowBand). NOT discovered as an example itself (leading underscore).

run_buy_algo does, in order:
  1. Read live BTC mark + sizing fields via rest.instrument("BTC").
  2. Size to a USD notional (default $250), floored to the instrument's lot.
  3. Build the algo's params from the live mark (per-algo callback).
  4. Disclose exactly what will be sent, with a Ctrl-C abort window (nothing
     sent yet).
  5. Fire EXACTLY ONCE via rest.execute(...) — one idempotency key up front.
  6. Poll rest.execution(id) for status, then cancel unless told otherwise.

These place REAL orders on mainnet. There is no testnet. The default params
arm each algo away from the market (a few % out), so within the short observe
window they rest rather than fill — then get cancelled.

Env: VIPER_API_KEY, VIPER_API_SECRET, VIPER_HANDLE, VIPER_WALLET (required);
     VIPER_EXAMPLE_USD (250), VIPER_EXAMPLE_ABORT_S (3),
     VIPER_EXAMPLE_OBSERVE_S (10), VIPER_EXAMPLE_POLL_S (2.0),
     VIPER_EXAMPLE_NO_CANCEL (set to leave it running).
"""
from __future__ import annotations

import os
import math
import uuid
import asyncio
from typing import Callable, Optional

from viper import ViperRestClient, ViperError

SYMBOL = "BTC"
USD = float(os.environ.get("VIPER_EXAMPLE_USD", "250"))
ABORT_S = float(os.environ.get("VIPER_EXAMPLE_ABORT_S", "3"))
OBSERVE_S = float(os.environ.get("VIPER_EXAMPLE_OBSERVE_S", "10"))
POLL_S = float(os.environ.get("VIPER_EXAMPLE_POLL_S", "2.0"))
NO_CANCEL = bool(os.environ.get("VIPER_EXAMPLE_NO_CANCEL"))


def require_env() -> None:
    missing = [k for k in ("VIPER_API_KEY", "VIPER_API_SECRET") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"# missing required env: {', '.join(missing)}")
    if not os.environ.get("VIPER_WALLET"):
        print("# note: VIPER_WALLET not set — the resolved wallet comes from your handle.")


def compute_size(usd: float, mark: float, sz_decimals: int, min_size: float) -> float:
    """USD notional -> base size, floored to the instrument's lot, min-clamped."""
    q = 10 ** int(sz_decimals)
    sized = math.floor((usd / mark) * q) / q
    if min_size and sized < min_size:
        sized = min_size
    return sized


async def instrument_mark(rest: ViperRestClient) -> tuple:
    """Return (mark, sz_decimals, min_order_value_size) for BTC."""
    rec = (await rest.instrument(SYMBOL))["instrument"]
    return (float(rec["mark_price"]), int(rec.get("sz_decimals", 5)),
            float(rec.get("min_order_value_size") or 0.0))


async def poll_and_cancel(rest: ViperRestClient, exec_id: str, label: str) -> None:
    """Poll execution status for the observe window, then cancel (unless
    VIPER_EXAMPLE_NO_CANCEL). Status/fill live under the `state` object."""
    deadline = asyncio.get_event_loop().time() + OBSERVE_S
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(max(2.0, POLL_S))
        try:
            st = await rest.execution(exec_id)
        except ViperError as e:
            print(f"#   status poll failed: {type(e).__name__} code={e.code}")
            break
        state = st.get("state") or {}
        print(f"#   status={state.get('status')} "
              f"filled={state.get('filled_size')}/{state.get('total_size')} "
              f"({state.get('filled_pct')}%)")
    if NO_CANCEL:
        print(f"# VIPER_EXAMPLE_NO_CANCEL set — leaving {exec_id} running.")
    else:
        try:
            c = await rest.cancel_execution(exec_id)
            print(f"# {label}: cancel -> {c.get('status') or c.get('result') or 'ok'}")
        except ViperError as e:
            print(f"# cancel failed: {type(e).__name__} code={e.code}")


async def run_buy_algo(*, algo: str, build_params: Callable[[float], dict],
                       label: str) -> None:
    """Size off live mark, disclose + abort window, fire one buy via REST, poll,
    cancel. `build_params(mark)` returns the per-algo params dict."""
    require_env()
    rest = ViperRestClient.from_env()
    async with rest:
        mark, sz_decimals, min_size = await instrument_mark(rest)
        size = compute_size(USD, mark, sz_decimals, min_size)
        params = build_params(mark)

        print(f"# {label}: LIVE buy {size} {SYMBOL} (~${USD:.0f} at mark {mark}) "
              f"on MAINNET — this places a real order.")
        print(f"#   params={params}")
        print(f"# Ctrl-C within {ABORT_S:.0f}s to abort (nothing sent yet)...")
        try:
            await asyncio.sleep(ABORT_S)
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\n# aborted — nothing was sent.")
            return

        idem = uuid.uuid4().hex
        try:
            res = await rest.execute(algo=algo, symbol=SYMBOL, side="buy",
                                     total_size=size, params=params,
                                     idempotency_key=idem)
        except ViperError as e:
            print(f"# execute failed: {type(e).__name__} code={e.code} status={e.status}")
            return

        exec_id = res.get("execution_id") or res.get("id")
        print(f"# {label}: launched execution_id={exec_id} status={res.get('status')}")
        if exec_id:
            await poll_and_cancel(rest, exec_id, label)

#!/usr/bin/env python3
"""
Detect a signal, then fire Glidemaker on BTC over REST — live mainnet.

The flagship REST example: it shows the whole bot-author loop end to end on the
typed REST client, no hand-rolled HMAC anywhere.

  1. Size to a USD notional off the live instrument (mark_price, sz_decimals,
     min size) via `rest.instrument("BTC")`.
  2. WATCH a signal source — poll `rest.price("BTC")` and look for upward
     momentum of >= SIGNAL_BPS over the watch window. No signal -> exit WITHOUT
     firing (the honest path: no edge, no trade).
  3. On signal: DISCLOSE exactly what will be sent, with a Ctrl-C abort window
     (nothing has been sent yet).
  4. FIRE EXACTLY ONCE via `rest.execute(...)` — one idempotency key generated
     up front (so any retry dedupes server-side) and a single-fire guard so the
     loop can't double-send.
  5. Poll `rest.execution(execution_id)` to show status, then cancel via
     `rest.cancel_execution(...)` unless told to leave it running.

Places a REAL order on mainnet (no testnet). Glidemaker is passive limit
execution — within a short window it may or may not fill.

Env (required):
    VIPER_API_KEY, VIPER_API_SECRET, VIPER_HANDLE, VIPER_WALLET
Knobs (optional):
    VIPER_EXAMPLE_USD         notional in USD          (default 250)
    VIPER_EXAMPLE_SIGNAL_BPS  upward move to trigger    (default 1.0 bps)
    VIPER_EXAMPLE_WATCH_S     max seconds to watch      (default 20)
    VIPER_EXAMPLE_POLL_S      poll cadence              (default 2.0)
    VIPER_EXAMPLE_ABORT_S     abort window after signal (default 3)
    VIPER_EXAMPLE_OBSERVE_S   seconds to watch the fill (default 10)
    VIPER_EXAMPLE_NO_CANCEL   set to leave it running   (default cancel)

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle
    export VIPER_WALLET=0x...
    viper-examples detect-and-fire-glidemaker
"""
import os
import math
import uuid
import asyncio

from viper import ViperRestClient, ViperError

ORDER = 5
SECTION = "Algorithms"
DESCRIPTION = "Detect a signal, then fire Glidemaker on BTC over REST — live ~$250, auto-cancels."

SYMBOL = "BTC"
USD = float(os.environ.get("VIPER_EXAMPLE_USD", "250"))
SIGNAL_BPS = float(os.environ.get("VIPER_EXAMPLE_SIGNAL_BPS", "1.0"))
WATCH_S = float(os.environ.get("VIPER_EXAMPLE_WATCH_S", "20"))
POLL_S = float(os.environ.get("VIPER_EXAMPLE_POLL_S", "2.0"))
ABORT_S = float(os.environ.get("VIPER_EXAMPLE_ABORT_S", "3"))
OBSERVE_S = float(os.environ.get("VIPER_EXAMPLE_OBSERVE_S", "10"))
NO_CANCEL = bool(os.environ.get("VIPER_EXAMPLE_NO_CANCEL"))


def _require_env():
    missing = [k for k in ("VIPER_API_KEY", "VIPER_API_SECRET") if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"# missing required env: {', '.join(missing)}")
    if not os.environ.get("VIPER_WALLET"):
        print("# note: VIPER_WALLET not set — the resolved wallet comes from your handle.")


def _compute_size(usd: float, mark: float, sz_decimals: int, min_size: float) -> float:
    """USD notional -> base size, floored to the instrument's lot, min-clamped."""
    q = 10 ** int(sz_decimals)
    sized = math.floor((usd / mark) * q) / q
    if min_size and sized < min_size:
        sized = min_size
    return sized


async def _detect_signal(rest: ViperRestClient) -> bool:
    """Poll price; return True on the first >= SIGNAL_BPS upward move from the
    watch baseline, False if the watch window elapses with no signal."""
    p0 = await rest.price(SYMBOL)
    baseline = float(p0["mid"])
    print(f"# watching {SYMBOL}: baseline mid={baseline} — need +{SIGNAL_BPS:.1f}bps "
          f"within {WATCH_S:.0f}s (no signal -> no trade)")
    deadline = asyncio.get_event_loop().time() + WATCH_S
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(POLL_S)
        p = await rest.price(SYMBOL)
        mid = float(p["mid"])
        move_bps = (mid - baseline) / baseline * 10_000
        print(f"#   mid={mid} move={move_bps:+.2f}bps")
        if move_bps >= SIGNAL_BPS:
            print(f"# SIGNAL: +{move_bps:.2f}bps >= {SIGNAL_BPS:.1f}bps")
            return True
    print("# no signal within the watch window — exiting without firing.")
    return False


async def main():
    _require_env()
    rest = ViperRestClient.from_env()
    async with rest:
        # 1) size off the live instrument. GET /v1/instruments/{symbol} wraps the
        #    record under "instrument" (alongside a validation_example).
        rec = (await rest.instrument(SYMBOL))["instrument"]
        mark = float(rec["mark_price"])
        size = _compute_size(USD, mark, rec.get("sz_decimals", 5),
                             float(rec.get("min_order_value_size") or 0))

        # 2) watch for the signal
        if not await _detect_signal(rest):
            return

        # 3) disclose + abort window (nothing sent yet)
        print(f"\n# Glidemaker: LIVE buy {size} {SYMBOL} (~${USD:.0f} at mark {mark}) "
              f"on MAINNET — this places a real order.")
        print(f"# Ctrl-C within {ABORT_S:.0f}s to abort (nothing sent yet)...")
        try:
            await asyncio.sleep(ABORT_S)
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\n# aborted — nothing was sent.")
            return

        # 4) fire EXACTLY ONCE. The fire is a single call outside any loop, and
        #    the idempotency key is generated up front and fixed — so even a
        #    transport-level retry of this exact call dedupes server-side instead
        #    of placing a second order.
        idem = uuid.uuid4().hex
        try:
            res = await rest.execute(
                algo="glidemaker", symbol=SYMBOL, side="buy", total_size=size,
                params={"strategy": "passive", "post_only": True},
                idempotency_key=idem,
            )
        except ViperError as e:
            print(f"# execute failed: {type(e).__name__} code={e.code} status={e.status}")
            return

        exec_id = res.get("execution_id") or res.get("id")
        print(f"# Glidemaker: launched execution_id={exec_id} status={res.get('status')}")

        # 5) poll execution status, then cancel (unless told to leave it running)
        if exec_id:
            deadline = asyncio.get_event_loop().time() + OBSERVE_S
            while asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(max(2.0, POLL_S))
                try:
                    st = await rest.execution(exec_id)
                except ViperError as e:
                    print(f"#   status poll failed: {type(e).__name__} code={e.code}")
                    break
                state = st.get("state") or {}
                print(f"#   execution status={state.get('status')} "
                      f"filled={state.get('filled_size')}/{state.get('total_size')} "
                      f"({state.get('filled_pct')}%) maker={state.get('maker_fill_size')}")
            if NO_CANCEL:
                print(f"# VIPER_EXAMPLE_NO_CANCEL set — leaving {exec_id} running.")
            else:
                try:
                    c = await rest.cancel_execution(exec_id)
                    print(f"# Glidemaker: cancel -> {c.get('status') or c.get('result') or 'ok'}")
                except ViperError as e:
                    print(f"# cancel failed: {type(e).__name__} code={e.code}")


if __name__ == "__main__":
    asyncio.run(main())

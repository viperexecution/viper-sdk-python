#!/usr/bin/env python3
"""
Stream a wallet's live orders, fills, and position/balance changes (account.state).

`account.state` is the per-wallet activity firehose: as orders rest, fill, and
positions move, the server pushes events on this one channel. This example
subscribes and renders each event type so you can watch trading activity live —
place an order in another window (or run `viper-examples place-order`) and see
the order_update / fill frames arrive here.

Event types rendered (from the account.state taxonomy):
    order_update            a resting/filled/cancelled order changed
    fill                    a trade executed (symbol/side/size/price/fee/maker)
    position_update         a position's size/PnL/mark changed
    balance_update          account equity/margin/withdrawable changed
    hip3_collateral_update  per-HIP-3-DEX collateral balances
    hydrated                control marker: initial snapshot done

This is a read-only stream — it places nothing. The minimal connect/subscribe
skeleton lives in `stream-account-state`; this one renders the payloads.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle        # optional
    export VIPER_WALLET=0x...              # the wallet to stream
    viper-examples stream-orders-fills

Tuning (optional env vars):
    VIPER_EXAMPLE_SECONDS=60   # how long to stream before closing (default 60)
"""
import os
import json
import asyncio

from viper import ViperWSClient

ORDER = 21
KIND = "ws"
SECTION = "Streaming (WebSocket reads)"
DESCRIPTION = "Stream a wallet's live orders/fills/positions (account.state), rendered."


def _num(v):
    return v if isinstance(v, (int, float)) else None


def _render(frame: dict) -> None:
    ev = frame.get("event")
    d = frame.get("data") or {}
    seq = frame.get("seq")

    if ev == "order_update":
        # Confirmed shape (account.state order_update): oid/cloid/symbol/side/size/
        # price/status/filled_size/remaining_size/order_type/trigger_price/
        # reduce_only/post_only/is_trigger/is_position_tpsl.
        flags = []
        if d.get("reduce_only"):
            flags.append("RO")
        if d.get("post_only"):
            flags.append("PO")
        if d.get("is_trigger"):
            flags.append("trigger")
        if d.get("is_position_tpsl"):
            flags.append("tpsl")
        trig = d.get("trigger_price")
        trig_s = f" trig={trig}" if trig is not None else ""
        flag_s = f" [{','.join(flags)}]" if flags else ""
        print(f"  order_update  {d.get('symbol')} {d.get('side')} {d.get('size')} "
              f"@ {d.get('price')}  {d.get('order_type')} {d.get('status')}"
              f"  filled={d.get('filled_size')} remaining={d.get('remaining_size')}"
              f"{trig_s}{flag_s}  oid={d.get('oid')}")

    elif ev == "fill":
        # Confirmed shape (account.state fill): wallet/oid/cloid/symbol/side/size/
        # price/fee/is_maker/timestamp. side is normalized to buy/sell; timestamp
        # is epoch seconds (float). closed_pnl/builder_fee may appear on some fills.
        liq = "maker" if d.get("is_maker") else "taker"
        extra = ""
        cpnl = _num(d.get("closed_pnl"))
        if cpnl is not None:
            extra += f" closed_pnl={cpnl:+.4f}"
        print(f"  fill          {d.get('symbol')} {d.get('side')} {d.get('size')} "
              f"@ {d.get('price')}  fee={d.get('fee')} ({liq}){extra}  oid={d.get('oid')}")

    elif ev == "position_update":
        upnl = _num(d.get("unrealized_pnl"))
        upnl_s = f"{upnl:+.4f}" if upnl is not None else "n/a"
        hip3 = f" [{d.get('dex_name')}]" if d.get("is_hip3") else ""
        print(f"  position      {d.get('symbol')}{hip3} {d.get('side')} {d.get('size')} "
              f"entry={d.get('entry_price')} mark={d.get('mark_price')} "
              f"uPnL={upnl_s} lev={d.get('leverage')}")

    elif ev == "balance_update":
        print(f"  balance       equity={d.get('account_value')} "
              f"margin_used={d.get('total_margin_used')} "
              f"withdrawable={d.get('withdrawable')}")

    elif ev == "hip3_collateral_update":
        bals = d.get("hip3_dex_balances") or []
        funded = [b for b in bals if _num(b.get("account_value"))]
        shown = ", ".join(f"{b.get('dex')}={b.get('account_value')}" for b in funded) or "all zero"
        print(f"  hip3_collat   {shown}")

    elif ev == "hydrated":
        print(f"  hydrated      initial snapshot complete "
              f"(events_emitted={d.get('events_emitted')})")

    else:
        # Any other event type — show channel/event/seq and the raw data so it's
        # never silently dropped.
        print(f"  {ev}  seq={seq}  {json.dumps(d, default=str)}")


async def main():
    wallet = os.environ.get("VIPER_WALLET", "").strip().lower()
    if not wallet:
        raise SystemExit("Set VIPER_WALLET to the wallet you want to stream.")
    seconds = float(os.environ.get("VIPER_EXAMPLE_SECONDS", "60"))

    def on_event(frame):
        # Route by data.wallet; control markers (e.g. hydrated) carry no
        # data.wallet, so fall back to scope_id.
        owner = (frame.get("data") or {}).get("wallet") or frame.get("scope_id")
        if owner and owner.lower() != wallet:
            return  # not our wallet — ignore (defensive; single-wallet connection)
        _render(frame)

    def on_terminal(code):
        print(f"# terminal close (code={code}) — stopping")

    client = ViperWSClient.from_env(
        wallet=wallet,
        on_event=on_event,
        on_terminal=on_terminal,
    )

    print(f"# streaming account.state for {wallet} for {seconds:g}s — "
          f"place an order elsewhere to see frames arrive\n")
    await client.start()
    # account.state is an actively-trading stream: mark it cadence_bearing so the
    # client's staleness watchdog applies.
    await client.subscribe("account.state", wallet, cadence_bearing=True)
    await asyncio.sleep(seconds)
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())

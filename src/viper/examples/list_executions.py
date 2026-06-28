#!/usr/bin/env python3
"""
List your algo executions with full detail — the reliable way to see everything.

GET /v1/executions returns every execution for your account (running, paused, and
finished) with rich per-execution detail: algo type, progress, fills, fees, and
benchmark deltas. This is the authoritative roster — unlike the live execution
stream, it never misses an algo and always carries the correct algo type.

Shows:
  1. a recent page of executions, rendered with the useful fields
  2. filtering by status and by algo (the `status` and `algo` query params)
  3. how to page (total_count / has_more / offset)

Read-only.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle        # optional
    viper-examples list-executions

Tuning (optional env vars):
    VIPER_EXAMPLE_LIMIT=10                  # page size (default 10)
    VIPER_EXAMPLE_STATUS=running            # demo a status filter (optional)
    VIPER_EXAMPLE_ALGO=glidemaker           # demo an algo filter (optional)
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperRestClient, ViperError

ORDER = 16
SECTION = "Account & Market Data"
DESCRIPTION = "List executions with full detail (GET /v1/executions) — the reliable execution roster."


def _row(e: dict) -> str:
    # algo_type is the reliable algo identifier on this endpoint.
    algo = e.get("algo_type") or e.get("algo") or "?"
    strat = e.get("strategy")
    strat_s = f"/{strat}" if strat and strat != "normal" else ""
    sym = e.get("symbol")
    side = e.get("side")
    status = e.get("status")
    total = e.get("total_size")
    filled = e.get("filled_size")
    prog = e.get("progress_pct")
    prog_s = f"{prog:.0f}%" if isinstance(prog, (int, float)) else "?"
    # grouped executions (baskets) carry a group_id / group_name
    grp = e.get("group_name") or e.get("group_id")
    grp_s = f"  basket={grp}" if grp else ""
    line = (f"  {algo}{strat_s}  {sym} {side}  {status}  "
            f"filled={filled}/{total} ({prog_s})  id={e.get('execution_id')}{grp_s}")
    # add execution-quality detail when the algo actually traded
    fills = (e.get("maker_fills") or 0) + (e.get("taker_fills") or 0)
    if fills:
        avg = e.get("avg_price")
        fees = e.get("total_fees")
        vsa = e.get("vs_arrival_bps")
        vsa_s = f"  vs_arrival={vsa:+.2f}bps" if isinstance(vsa, (int, float)) else ""
        dur = e.get("duration_seconds")
        dur_s = f"  {dur:.0f}s" if isinstance(dur, (int, float)) else ""
        line += (f"\n      avg={avg}  fees={fees}  "
                 f"maker={e.get('maker_fills')}/taker={e.get('taker_fills')}{vsa_s}{dur_s}")
    return line


def _print_page(resp: dict, header: str) -> None:
    items = resp.get("items") or []
    total = resp.get("total_count")
    has_more = resp.get("has_more")
    offset = resp.get("offset")
    print(f"\n{header}  (showing {len(items)} of {total}, offset={offset}, has_more={has_more})")
    if not items:
        print("  (none)")
        return
    for e in items:
        print(_row(e))


async def main() -> None:
    limit = int(os.environ.get("VIPER_EXAMPLE_LIMIT", "10"))
    status = os.environ.get("VIPER_EXAMPLE_STATUS", "").strip()
    algo = os.environ.get("VIPER_EXAMPLE_ALGO", "").strip()

    rest = ViperRestClient.from_env()
    async with rest:
        # 1) a recent page — the full roster, every algo, correct types
        page = await rest.executions(limit=limit, offset=0)
        _print_page(page, "1) RECENT EXECUTIONS")

        # 2) filters — `status` and `algo` narrow the list server-side.
        #    (status: running | paused | completed | cancelled | interrupted |
        #     error | pending;  algo: pacemaker | glidemaker | ghostsweep |
        #     flowscale | flowband | smart_exit)
        if status:
            f = await rest.executions(limit=limit, status=status)
            _print_page(f, f"2a) FILTER status={status}")
        if algo:
            f = await rest.executions(limit=limit, algo=algo)
            _print_page(f, f"2b) FILTER algo={algo}")
        if not status and not algo:
            print("\n2) FILTERS: set VIPER_EXAMPLE_STATUS (e.g. running) or "
                  "VIPER_EXAMPLE_ALGO (e.g. glidemaker) to narrow the list server-side.")

        # 3) pagination — when has_more is true, advance offset by limit
        if page.get("has_more"):
            nxt = await rest.executions(limit=limit, offset=limit)
            _print_page(nxt, f"3) NEXT PAGE (offset={limit})")
        else:
            print(f"\n3) PAGINATION: has_more=False — all executions fit in one page "
                  f"of {limit}. (When true, request offset={limit}, then {2*limit}, ...)")


if __name__ == "__main__":
    asyncio.run(main())

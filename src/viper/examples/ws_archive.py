#!/usr/bin/env python3
"""
Archive and unarchive finished executions over the socket — Tier-3 housekeeping.

Once executions finish, you archive them to declutter your active list. These
commands do that over the socket:

    archive_execution        archive one finished execution
    unarchive_execution      bring it back
    archive_executions_bulk  archive several by id (atomic: if ANY id is foreign
                             to the connection's wallet, the WHOLE batch is
                             rejected and nothing is mutated)

This example finds four of YOUR already-finished executions (read-only via REST),
archives one singly + three in bulk, then unarchives two — leaving two visibly
archived so the state change is easy to confirm in the UI. No orders, no positions.
(Unarchive the remaining two from the UI whenever, or leave them archived.)

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle        # optional
    export VIPER_WALLET=0x...              # one wallet per connection
    viper-examples ws-archive
"""
from __future__ import annotations

import os
import asyncio

from viper import ViperWSClient, ViperRestClient


ORDER = 32
KIND = "ws"
SECTION = "Trading over WebSocket (Tier-3 writes)"
DESCRIPTION = "Archive / unarchive finished executions over the socket (Tier-3: archive/unarchive/bulk)."


async def _cmd(client, command: str, **fields):
    frame = {"command": command, **fields}
    r = await client.send_command(frame)
    if r is None:
        print(f"  {command}: (no response within timeout)")
        return None
    if r.get("result") == "error":
        print(f"  {command}: ERROR {r.get('code')}: {r.get('message')}")
        for k in ("mismatched_ids",):
            if (r.get("details") or {}).get(k):
                print(f"      {k}: {r['details'][k]}")
        return None
    return r


async def main() -> None:
    wallet = os.environ.get("VIPER_WALLET", "").strip().lower()
    if not wallet:
        raise SystemExit("Set VIPER_WALLET to the wallet to use.")

    # Find finished, not-yet-archived executions to act on (read-only).
    async with ViperRestClient.from_env() as rest:
        page = await rest.executions(limit=25)
        items = page.get("items") or []
    finished = [e for e in items
                if e.get("status") in ("completed", "stopped", "cancelled", "error")
                and not e.get("archived")]
    if len(finished) < 4:
        raise SystemExit(f"Need 4 finished, unarchived executions; found {len(finished)}. "
                         "Run a few algo examples first, then retry.")
    ids = [e.get("execution_id") for e in finished[:4] if e.get("execution_id")]

    client = ViperWSClient.from_env(wallet=wallet, on_event=lambda f: None,
                                    on_terminal=lambda c: None)
    print(f"# archive housekeeping over one WS connection ({wallet})\n")
    print(f"# acting on {len(ids)} finished execution(s)\n")
    await client.start()
    try:
        # 1) ARCHIVE one (single command).
        print(f"1) ARCHIVE (single)  {ids[0]}")
        r = await _cmd(client, "archive_execution", execution_id=ids[0])
        if r:
            print(f"   result={r.get('result')}")

        # 2) BULK ARCHIVE the other 3 (atomic-reject if any id is foreign).
        rest_ids = ids[1:]
        print(f"\n2) BULK ARCHIVE  {len(rest_ids)} ids")
        r = await _cmd(client, "archive_executions_bulk", execution_ids=rest_ids)
        if r:
            d = r.get("data") or {}
            print(f"   result={r.get('result')}  "
                  f"archived={d.get('archived_count', d.get('count'))}  "
                  f"errors={d.get('error_count')}")
        print(f"   >>> check the UI: all {len(ids)} executions are now archived. Waiting 8s...")
        await asyncio.sleep(8)

        # 3) UNARCHIVE only 2 — leaving 2 visibly archived (a clear net change).
        unarchive = ids[:2]
        keep_archived = ids[2:]
        print(f"\n3) UNARCHIVE  {len(unarchive)} of them (leaving {len(keep_archived)} archived)")
        for eid in unarchive:
            ur = await _cmd(client, "unarchive_execution", execution_id=eid)
            if ur:
                print(f"   {eid}: {ur.get('result')}")
            await asyncio.sleep(2)
        print(f"\n   >>> NET RESULT: {len(keep_archived)} still archived, "
              f"{len(unarchive)} back in active.")
        print(f"   still archived: {keep_archived}")
        print("   (unarchive these from the UI whenever — or leave them; your call.)")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())

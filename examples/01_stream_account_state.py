#!/usr/bin/env python3
"""
Example 01 — stream account.state and route frames by data.wallet.

The minimal end-to-end shape every Viper bot starts from: connect, subscribe,
handle live frames, shut down cleanly. The fuller "detect signal -> fire
Glidemaker" example builds on this skeleton.

Run:
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=...
    export VIPER_HANDLE=your-handle
    export VIPER_WALLET=0x...
    python examples/01_stream_account_state.py
"""
import os
import asyncio
from viper import ViperWSClient


async def main():
    api_key = os.environ["VIPER_API_KEY"]
    api_secret = os.environ["VIPER_API_SECRET"]
    handle = os.environ.get("VIPER_HANDLE", "")
    wallet = os.environ["VIPER_WALLET"].lower()

    def on_event(frame):
        ev = frame.get("event")
        # Route DATA frames by data.wallet. Control markers (e.g. `hydrated`)
        # carry no data.wallet — fall back to scope_id for those.
        owner = (frame.get("data") or {}).get("wallet") or frame.get("scope_id")
        print(f"[{frame.get('channel')}/{ev}] seq={frame.get('seq')} owner={owner}")

    def on_terminal(code):
        print(f"# terminal close (code={code}) — stopping")

    client = ViperWSClient(
        api_key_id=api_key,
        api_secret=api_secret,
        handle=handle,
        wallet=wallet,
        on_event=on_event,
        on_terminal=on_terminal,
    )

    await client.start()
    await client.subscribe("account.state", wallet)
    # account.state hydration is server-slow (~5s) — give it room before you
    # conclude anything about the stream.
    await asyncio.sleep(30)
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())

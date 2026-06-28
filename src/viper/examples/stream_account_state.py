#!/usr/bin/env python3
"""
Stream account.state and route frames by data.wallet.

The minimal end-to-end shape every Viper bot starts from: connect, subscribe,
handle live frames, shut down cleanly. The fuller "detect signal -> fire
Glidemaker" examples build on this skeleton.

Run (after `pip install viper-execution`):
    export VIPER_API_KEY=vk_...
    export VIPER_API_SECRET=vs_...
    export VIPER_HANDLE=your-handle        # optional
    export VIPER_WALLET=0x...              # the wallet to stream
    viper-examples stream-account-state
"""
import os
import asyncio

from viper import ViperWSClient

ORDER = 20
KIND = "ws"
SECTION = "Streaming (WebSocket reads)"
DESCRIPTION = "Stream account.state; route frames by data.wallet."


async def main():
    wallet = os.environ.get("VIPER_WALLET", "").strip().lower()
    if not wallet:
        raise SystemExit("Set VIPER_WALLET to the wallet you want to stream.")

    def on_event(frame):
        ev = frame.get("event")
        # Route DATA frames by data.wallet. Control markers (e.g. `hydrated`)
        # carry no data.wallet — fall back to scope_id for those.
        owner = (frame.get("data") or {}).get("wallet") or frame.get("scope_id")
        print(f"[{frame.get('channel')}/{ev}] seq={frame.get('seq')} owner={owner}")

    def on_terminal(code):
        print(f"# terminal close (code={code}) — stopping")

    # from_env() reads VIPER_API_KEY / VIPER_API_SECRET / VIPER_HANDLE.
    # Explicit kwargs (wallet, callbacks) override the environment.
    client = ViperWSClient.from_env(
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

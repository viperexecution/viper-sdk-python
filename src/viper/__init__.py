"""Viper Execution Python SDK.

Institutional-grade client for the Viper Execution trading API on Hyperliquid.

This release ships the resilient WebSocket client (`ViperWSClient`) and the
resync REST-fetch mapping. The full typed REST client lands in a subsequent
release.

Quickstart:

    import asyncio
    from viper import ViperWSClient

    async def main():
        client = ViperWSClient(
            api_key_id="vk_...",
            api_secret="...",
            handle="your-handle",
            wallet="0x...",
            on_event=lambda f: print(f["channel"], f.get("event")),
        )
        await client.start()
        await client.subscribe("account.state", "0x...")
        await asyncio.sleep(30)
        await client.close()

    asyncio.run(main())

Note: the SDK version is independent of the API version. This is SDK 0.x
against API v1.
"""

__version__ = "0.1.0"

from .ws import (
    ViperWSClient,
    RESYNC_ENDPOINTS,
    resync_endpoint,
    make_rest_fetcher,
)

__all__ = [
    "ViperWSClient",
    "RESYNC_ENDPOINTS",
    "resync_endpoint",
    "make_rest_fetcher",
    "__version__",
]

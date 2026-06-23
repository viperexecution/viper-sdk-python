"""Viper Execution Python SDK.

Institutional-grade client for the Viper Execution trading API on Hyperliquid.

This release ships the resilient WebSocket client (`ViperWSClient`) and the
typed REST client (`ViperRestClient`).

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

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("viper-execution")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"

from .ws import (
    ViperWSClient,
    RESYNC_ENDPOINTS,
    resync_endpoint,
    make_rest_fetcher,
)
from .rest import ViperRestClient
from .exceptions import (
    ViperError,
    ViperAuthError,
    ViperConnectionError,
    ViperRateLimitError,
    ViperAPIError,
    ViperValidationError,
    ViperConflictError,
    ViperNotFoundError,
)

__all__ = [
    "ViperWSClient",
    "ViperRestClient",
    "RESYNC_ENDPOINTS",
    "resync_endpoint",
    "make_rest_fetcher",
    "ViperError",
    "ViperAuthError",
    "ViperConnectionError",
    "ViperRateLimitError",
    "ViperAPIError",
    "ViperValidationError",
    "ViperConflictError",
    "ViperNotFoundError",
    "__version__",
]

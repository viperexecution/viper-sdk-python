# Viper Execution Python SDK

Institutional-grade Python client for the [Viper Execution](https://viperexecution.com) trading API on Hyperliquid.

> **Status:** SDK `0.1.1` (beta). Ships the resilient WebSocket client and the resync REST-fetch mapping. The full typed REST client lands in a subsequent release. The SDK version is independent of the API version — this is SDK 0.x against API v1.

## Install

```bash
pip install viper-execution
```

Requires Python ≥ 3.10.

## Quickstart

```python
import os
import asyncio
from viper import ViperWSClient

async def main():
    # from_env() reads VIPER_API_KEY / VIPER_API_SECRET / VIPER_HANDLE /
    # VIPER_WALLET. Pass anything explicitly to override the environment.
    wallet = os.environ["VIPER_WALLET"].lower()
    client = ViperWSClient.from_env(
        on_event=lambda f: print(f["channel"], f.get("event")),
    )
    await client.start()
    await client.subscribe("account.state", wallet)
    await asyncio.sleep(30)
    await client.close()

asyncio.run(main())
```

Prefer to pass credentials directly instead of via the environment? The
constructor takes them explicitly:

```python
client = ViperWSClient(
    api_key_id="vk_...",
    api_secret="...",
    handle="your-handle",
    wallet="0x...",
    on_event=lambda f: print(f["channel"], f.get("event")),
)
```

## Runnable examples

Examples ship inside the package — no extra downloads. List the catalog and
run one by name or number:

```bash
viper-examples                          # list the catalog
viper-examples stream-account-state     # run by name
viper-examples 01                       # ...or by number
```

Set the env vars the examples read:

```bash
export VIPER_API_KEY=vk_...
export VIPER_API_SECRET=vs_...
export VIPER_HANDLE=your-handle     # optional
export VIPER_WALLET=0x...           # the wallet to stream
```

The source for each example lives in
[`src/viper/examples/`](src/viper/examples/).

## What the WebSocket client handles for you

The `/v1/ws` stream has a number of behaviors a naive client gets wrong. `ViperWSClient` handles them as a built-in contract:

- **Liveness** — transport ping/pong plus a data-staleness watchdog; silent half-open connections are detected and reconnected.
- **Reconnect with resume** — on drop, it reconnects with exponential backoff and resubscribes every scope carrying its `last_seq` cursor, so you resume exactly where you left off (replay from the per-scope ring buffer).
- **Resync recovery** — when the server can't satisfy a cursor (`buffer_overflow` / `last_seq_ahead_of_server` / `scope_not_found`), it REST-fetches authoritative current state and resubscribes fresh.
- **Multi-wallet attribution** — every data frame is routed by `data.wallet`, so one socket can carry many wallets without cross-attribution. (Control markers such as `hydrated` carry no `data.wallet`; route those by `scope_id`.)
- **Slow hydration** — `account.state` hydration is server-slow (~5s; it gathers balance + HIP-3 collateral across all dexes, then bursts frames). The client does not mistake that for a dead stream, and neither should your application logic.
- **Terminal conditions** — credential revocation (close `4013`), a handshake auth rejection (HTTP `401`/`403` — revoked/invalid key or insufficient scope), or an exhausted reconnect budget all stop the loop permanently via `on_terminal` rather than reconnect-hammering.

## Callbacks

| Callback | Fires on |
|---|---|
| `on_event(frame)` | Every classified data frame (the main path) |
| `on_meta(frame)` | `_meta` frames: welcome + upstream connectivity events |
| `on_terminal(code)` | Terminal stop. `code` is the WS close code (`4013` = credentials revoked), the handshake HTTP status (`401`/`403`), or `-1` (reconnect budget exhausted) |
| `on_command_result(frame)` | Subscribe acks / command errors (no correlation id) |
| `on_raw(frame)` | Optional advanced tap: every frame pre-classification (audit/metrics) |

## License

MIT — see [LICENSE](LICENSE).

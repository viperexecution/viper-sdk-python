# Viper Execution Python SDK

Institutional-grade Python client for the [Viper Execution](https://viperexecution.com) trading API on Hyperliquid.

> **Status:** SDK `0.2.x`. Ships a typed async REST client (`ViperRestClient`) and a resilient WebSocket client (`ViperWSClient`). The SDK version is independent of the API version — this is SDK 0.x against API v1.

The SDK is a convenience layer over the raw HMAC + REST/WebSocket surface — never required. Every response is returned as a plain `dict`, so you are never boxed out of the raw payload; the typed signatures and `TypedDict` hints are there for editor and type-checker support only.

## Install

```bash
pip install viper-execution
```

Requires Python ≥ 3.10.

## Quickstart (REST)

```python
import asyncio
from viper import ViperRestClient

async def main():
    # from_env() reads VIPER_API_KEY / VIPER_API_SECRET / VIPER_HANDLE /
    # VIPER_WALLET. Pass anything explicitly to override the environment.
    async with ViperRestClient.from_env() as viper:
        # market data
        btc = (await viper.instrument("BTC"))["instrument"]
        print("BTC mark:", btc["mark_price"])

        # launch a Glidemaker (idempotency key is auto-generated)
        res = await viper.execute(
            algo="glidemaker", symbol="BTC", side="buy", total_size=0.001,
            params={"strategy": "neutral", "limit_price": 65000},
        )
        print(res["execution_id"], res["status"])

asyncio.run(main())
```

## Quickstart (WebSocket)

```python
import os
import asyncio
from viper import ViperWSClient

async def main():
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

## Using credentials

Both clients take credentials two ways, both first-class — pick whichever fits how your process gets its secrets.

**From the environment (quickest, and production-correct).** `from_env()` reads `VIPER_API_KEY`, `VIPER_API_SECRET`, `VIPER_HANDLE`, and `VIPER_WALLET`. This is also the right pattern for deployment: containers, CI, and secret managers all inject secrets as env vars, so the same code runs unchanged from laptop to production. Anything passed explicitly overrides the environment:

```python
viper = ViperRestClient.from_env(handle="override-handle")
```

**Explicitly (your own secret store).** If your keys live in Vault, AWS Secrets Manager, an HSM, or a config file, fetch them in your code and pass them to the constructor directly — `from_env()` is never required:

```python
api_key_id, api_secret = my_secret_store.get("viper")  # however you fetch them
viper = ViperRestClient(
    api_key_id=api_key_id,
    api_secret=api_secret,
    handle="your-handle",
    wallet="0x...",
)
```

`ViperWSClient` constructs identically.

## Using the REST client

`ViperRestClient` is async and instance-based (no global singleton). It covers the core trading surface: execute and executions, orders, account, positions, market data (instruments, price, orderbook), leverage, and limits.

```python
async with ViperRestClient.from_env() as viper:
    # reads — return the raw dict as-is
    state   = await viper.account_state()
    pos     = await viper.positions()
    price   = await viper.price("BTC")

    # mutating calls auto-generate an Idempotency-Key and are throttled
    order   = await viper.place_order(symbol="BTC", side="buy", size=0.001,
                                      order_type="limit", price=60000, post_only=True)
    await viper.cancel_order(symbol="BTC", order_id=order["order_ids"][0])
```

What the client handles for you:

- **Signing** — HMAC-SHA256 over the canonical `{timestamp}{method}{path}{body}`, signed over the exact bytes sent. You never construct a signature.
- **Idempotency** — every mutating call (`execute`, `place_order`, `cancel_*`, `modify_order`, `close_*`, execution lifecycle, `set_leverage`, `update_settings`, `nuke`) auto-generates an `Idempotency-Key` unless you pass one. Reads don't.
- **Replay spacing** — a per-instance throttle keeps mutating calls ≥ 1.1s apart; reads are never blocked.
- **Configurable transport** — inject your own `httpx.AsyncClient` via `http_client=...` to set timeouts, proxies, or pools.

`nuke()` (cancel all orders + close all positions) requires `confirm=True` with no default — the conscious step the raw API gets from its mandatory `Idempotency-Key`, which the client otherwise fills for you.

### Errors

The API error envelope is mapped to typed exceptions, all subclasses of `ViperError`:

| Exception | Maps from |
|---|---|
| `ViperValidationError` | `validation_error`, `bad_request`, `missing_field`, … (400/422) |
| `ViperAuthError` | `unauthorized`, `insufficient_scope`, `forbidden`, `tenancy_denied` (401/403) |
| `ViperConflictError` | `conflict`, `idempotency_mismatch`, `state_transition_forbidden` (409) |
| `ViperNotFoundError` | `not_found`, `unknown_route`, `scope_not_found` (404) |
| `ViperRateLimitError` | `rate_limited`, `venue_rate_limit` (429) — carries `retry_after` |
| `ViperAPIError` | anything else |

Every exception carries `.code` (the machine-readable error code), `.status`, and `.payload`, so you can branch precisely — e.g. tell a state `conflict` from an `idempotency_mismatch`:

```python
from viper import ViperConflictError

try:
    await viper.execute(algo="glidemaker", symbol="BTC", side="buy",
                        total_size=0.001, params={"strategy": "neutral"})
except ViperConflictError as e:
    if e.code == "idempotency_mismatch":
        ...  # reused key with a different body — a client bug
```

## Runnable examples

Examples ship inside the package — no extra downloads. List the catalog and
run one by name or number:

```bash
viper-examples                          # list the catalog
viper-examples stream-account-state     # run by name
viper-examples 01                       # ...or by number
```

Set the env vars the examples read — bash/zsh:

```bash
export VIPER_API_KEY=vk_...
export VIPER_API_SECRET=vs_...
export VIPER_HANDLE=your-handle     # optional
export VIPER_WALLET=0x...           # the wallet to trade/stream
```

…or PowerShell:

```powershell
$env:VIPER_API_KEY = "vk_..."
$env:VIPER_API_SECRET = "vs_..."
$env:VIPER_HANDLE = "your-handle"   # optional
$env:VIPER_WALLET = "0x..."         # the wallet to trade/stream
```

### Live algo examples

Several examples fire a real algo. They place **real orders on mainnet** (there
is no testnet). Each reads the live BTC mark, sizes to a USD notional (default
~$250), discloses exactly what it will do with a short Ctrl-C abort window, fires
**once**, observes, then cancels.

Over the WebSocket command surface:

```bash
viper-examples start-glidemaker     # passive limit
viper-examples start-pacemaker      # TWAP
```

Over the REST client:

```bash
viper-examples detect-and-fire-glidemaker   # poll a signal, then fire on it
viper-examples start-ghostsweep             # hidden stop
viper-examples start-flowscale              # scaled ladder
viper-examples start-flowband               # floating stealth scale
viper-examples smart-exit                   # reduce-only stop on an existing long
```

Optional knobs:

```bash
export VIPER_EXAMPLE_USD=250         # target notional (default 250)
export VIPER_EXAMPLE_OBSERVE_S=10    # seconds to observe before cancel (default 10)
export VIPER_EXAMPLE_NO_CANCEL=1     # leave the execution running instead of cancelling
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

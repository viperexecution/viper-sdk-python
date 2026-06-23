#!/usr/bin/env python3
"""
ws.py — Viper v1 resilient WebSocket client (reference implementation)

This is the canonical resilient consumer for Viper's `/v1/ws` bot-dev stream.
It is built to be read top-to-bottom: it doubles as the SDK's WS half and as
the backbone of the streaming examples.

Design
------
The resilience skeleton (two-layer liveness, watchdog, fire-and-forget
reconnect, exponential backoff, resubscribe-all) uses production-proven
defaults; the thresholds below are battle-tested values.

The protocol layer (HMAC handshake, welcome/_meta consumption, per-scope
`last_seq` cursors, resync recovery, `data.wallet` routing, `4013` terminal)
is specific to `/v1/ws` and follows the Streams reference + the resilience
contract. Hyperliquid itself has no ring-buffer/resync model, so the replay
layer is Viper-specific.

Two-layer liveness (the key pattern)
------------------------------------------------------------
1. Transport (universal): the `websockets` library's own ping_interval /
   ping_timeout detects a dead or half-open peer in <=15s on EVERY connection,
   including idle ones. The library auto-answers server pings and does NOT
   surface them to the app message loop.
2. Data-staleness (cadence-bearing subscriptions only): an app-level timer
   that fires ONLY for subscriptions expected to push continuously (a running
   `execution.state`, an actively-trading `account.state`). For event-only /
   idle subscriptions, silence is normal — a data-silence timer would
   false-positive, so they rely solely on layer 1.

Usage
-----
    client = ViperWSClient(
        api_key_id=..., api_secret=..., handle=...,
        on_event=lambda f: ...,          # channel events (route by data.wallet)
        on_meta=lambda f: ...,           # _meta upstream transitions (info only)
        on_terminal=lambda code: ...,    # 4013 revocation — do not reconnect
        rest_fetch_current_state=async (channel, scope_id) -> None,  # resync recovery
    )
    await client.start()                                 # connect + run forever
    await client.subscribe("execution.state", exec_id, cadence_bearing=True)
    await client.subscribe("account.state", wallet, cadence_bearing=True)

Deps: pip install websockets httpx
"""

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, Optional, Tuple

import websockets


# --------------------------------------------------------------------------
# Handshake signing — wire-verified against the live /v1/ws handshake.
# Do not reconstruct from memory.
# Re-auth happens ONLY at handshake; frames are not individually signed.
# --------------------------------------------------------------------------
def _handshake_headers(api_key_id: str, api_secret: str, ws_path: str,
                       handle: Optional[str], wallet: Optional[str]) -> Dict[str, str]:
    """Fresh signed headers for a `/v1/ws` upgrade. Signs `"{ts}GET{ws_path}"`,
    no body. A new ts is generated per call, so every (re)connect re-signs."""
    ts = str(int(time.time()))
    payload = f"{ts}GET{ws_path}".encode()
    sig = hmac.new(api_secret.encode(), payload, hashlib.sha256).hexdigest()
    h = {
        "X-Viper-Api-Key-Id": api_key_id,
        "X-Viper-Signature": sig,
        "X-Viper-Timestamp": ts,
    }
    if handle:
        h["X-Viper-Handle"] = handle
    if wallet:
        # Disambiguates which wallet on a multi-wallet connection.
        h["Viper-Wallet"] = wallet
    return h


def _handshake_status(exc: Exception) -> Optional[int]:
    """Extract the HTTP status from a `websockets` handshake-rejection exception,
    across library versions: `InvalidStatus.response.status_code` (>=11) or
    `InvalidStatusCode.status_code` (older). Returns None for transport/network
    errors that carry no HTTP status (those are transient -> reconnect)."""
    resp = getattr(exc, "response", None)
    code = getattr(resp, "status_code", None)
    if isinstance(code, int):
        return code
    code = getattr(exc, "status_code", None)
    return code if isinstance(code, int) else None


# --------------------------------------------------------------------------
# Resync recovery: channel -> REST current-state endpoint.
# When the server returns {"result":"resync"} for a scope, the client REST-fetches
# the authoritative current state (to bridge the ~5s re-hydration gap), then
# resubscribes WITHOUT last_seq. Mapping wire-verified against the live REST
# surface: every row GETs 200 with the expected shape — monitor.stats/live-stats
# returns fire_count/max_fires,
# distinct from the monitor-detail shape the other monitor.* rows return).
#
# _meta and basket.event are deliberately ABSENT: both are live-only with no ring
# buffer, so they never emit `resync` — resync_endpoint() returns None for them
# and the client skips REST-fetch (the fresh resubscribe is the only recovery).
# --------------------------------------------------------------------------
RESYNC_ENDPOINTS: Dict[str, str] = {
    "account.state":        "/v1/account/state",
    "execution.state":      "/v1/executions/{scope_id}",
    "execution.chart":      "/v1/executions/{scope_id}",    # chart state via exec detail
    "execution.list":       "/v1/executions",
    "monitor.event":        "/v1/monitors/{scope_id}",      # monitor-resource endpoint
    "monitor.stats":        "/v1/monitors/{scope_id}/live-stats",  # per-monitor live stats
    "monitor.state_change": "/v1/monitors/{scope_id}",
    "monitor.alert":        "/v1/monitors/{scope_id}",
}


def resync_endpoint(channel: str, scope_id: str) -> Optional[str]:
    """REST current-state path for a channel's resync recovery, or None if the
    channel has no ring buffer (no resync path) — _meta, basket.event."""
    tmpl = RESYNC_ENDPOINTS.get(channel)
    return None if tmpl is None else tmpl.replace("{scope_id}", scope_id)


def make_rest_fetcher(base_url: str, api_key_id: str, api_secret: str,
                      handle: Optional[str] = None, wallet: Optional[str] = None):
    """Build a `rest_fetch_current_state(channel, scope_id)` coroutine that GETs
    the channel's current-state endpoint over the signed REST surface. Reference
    implementation using httpx (imported lazily so the WS core has no hard dep);
    bot-devs can inject their own fetcher instead. Returns parsed JSON or None."""
    import uuid

    async def fetch(channel: str, scope_id: str):
        path = resync_endpoint(channel, scope_id)
        if path is None:
            return None  # live-only channel; nothing to REST-fetch
        import httpx
        # GET nonce to dodge any same-second idempotency collisions, then HMAC-sign.
        nonce_path = f"{path}{'&' if '?' in path else '?'}_n={uuid.uuid4().hex[:8]}"
        ts = str(int(time.time()))
        sig = hmac.new(api_secret.encode(), f"{ts}GET{nonce_path}".encode(),
                       hashlib.sha256).hexdigest()
        headers = {"X-Viper-Api-Key-Id": api_key_id, "X-Viper-Signature": sig,
                   "X-Viper-Timestamp": ts}
        if handle:
            headers["X-Viper-Handle"] = handle
        if wallet:
            headers["Viper-Wallet"] = wallet
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{base_url}{nonce_path}", headers=headers, timeout=30)
            return r.json() if r.status_code == 200 else None

    return fetch


@dataclass
class _Subscription:
    """One (channel, scope_id) subscription and its resume cursor.

    `last_seq` is tracked PER (channel, scope_id) — never a single global
    cursor — because `seq` is monotonic per scope_id and resync is per scope.
    """
    channel: str
    scope_id: str
    last_seq: Optional[int] = None
    cadence_bearing: bool = False  # subject to the data-staleness watchdog?


class ViperWSClient:
    # --- Thresholds: production-proven defaults ---
    PING_INTERVAL = 10          # client-side keepalive ping (transport liveness)
    PING_TIMEOUT = 5            # dead-peer detection window
    CLOSE_TIMEOUT = 3
    WATCHDOG_INTERVAL = 10      # health check cadence
    DATA_STALE_THRESHOLD = 45   # cadence-bearing silence -> reconnect (STALE_TIMEOUT=45)
    MAX_RECONNECT_ATTEMPTS = 10
    RECONNECT_BASE_DELAY = 1.0  # backoff = min(BASE * 2**(attempt-1), 30)

    def __init__(
        self,
        api_key_id: str,
        api_secret: str,
        handle: Optional[str] = None,
        wallet: Optional[str] = None,
        ws_url: str = "wss://api.viperexecution.com/v1/ws",
        on_event: Optional[Callable[[dict], None]] = None,
        on_meta: Optional[Callable[[dict], None]] = None,
        on_terminal: Optional[Callable[[int], None]] = None,
        on_command_result: Optional[Callable[[dict], None]] = None,
        on_raw: Optional[Callable[[dict], None]] = None,
        rest_fetch_current_state: Optional[
            Callable[[str, str], Awaitable[None]]
        ] = None,
    ):
        self._api_key_id = api_key_id
        self._api_secret = api_secret
        self._handle = handle
        self._wallet = wallet
        self._ws_url = ws_url
        self._ws_path = "/v1/ws" if ws_url.endswith("/v1/ws") else ws_url.split("://", 1)[-1].split("/", 1)[-1]
        if not self._ws_path.startswith("/"):
            self._ws_path = "/" + self._ws_path

        # Callbacks
        self._on_event = on_event or (lambda f: None)
        self._on_meta = on_meta or (lambda f: None)
        self._on_terminal = on_terminal or (lambda code: None)
        # Un-correlated command results (subscribe acks/errors carry no
        # client_correlation_id — they can only be matched by channel/scope_id).
        self._on_command_result = on_command_result or (lambda f: None)
        # Optional raw-frame tap: fires for every frame before classification.
        # Advanced hook for audit trails, custom metrics, or wire logging.
        self._on_raw = on_raw or (lambda f: None)
        self._rest_fetch_current_state = rest_fetch_current_state

        # Connection state
        self.ws = None
        self._connected = False
        self._connecting = False
        self._intentionally_closed = False
        self._terminal = False           # 4013 revocation: stop forever
        self._reconnect_attempts = 0
        self.connect_count = 0           # total successful connects (operational metric)

        # Subscriptions, keyed (channel, scope_id) -> _Subscription
        self._subs: Dict[Tuple[str, str], _Subscription] = {}

        # Command correlation: client_correlation_id -> Future(result frame)
        self._pending: Dict[str, asyncio.Future] = {}

        # Welcome-advertised limits (refreshed each connect)
        self.ring_buffer_size: Optional[int] = None
        self.subscriptions_max: Optional[int] = None
        self.resolved_wallet: Optional[str] = None

        # Liveness bookkeeping
        self.last_message_at: Optional[float] = None
        self._connect_lock: Optional[asyncio.Lock] = None

        # Background tasks
        self._reader_task: Optional[asyncio.Task] = None
        self._watchdog_task: Optional[asyncio.Task] = None

        # Welcome handshake — the reader (sole socket consumer) surfaces the
        # first frame via this Event so connect() can absorb the limits.
        self._welcome: Optional[dict] = None
        self._welcome_event: Optional[asyncio.Event] = None

    # ----------------------------------------------------------------- utils
    def _lock(self) -> asyncio.Lock:
        if self._connect_lock is None:
            self._connect_lock = asyncio.Lock()
        return self._connect_lock

    @property
    def is_connected(self) -> bool:
        """True liveness — checks the real socket state, not a flag."""
        if not self._connected or self.ws is None:
            return False
        try:
            from websockets.protocol import State
            return self.ws.state == State.OPEN
        except (AttributeError, ImportError):
            try:
                return not self.ws.closed
            except AttributeError:
                return self._connected

    def _has_cadence_bearing_subs(self) -> bool:
        """Any subscription expected to push continuously? The data-staleness
        watchdog applies ONLY to these; idle/event-only subs rely on ping/pong."""
        return any(s.cadence_bearing for s in self._subs.values())

    # --------------------------------------------------------------- connect
    async def start(self):
        """Connect and run until terminal (4013) or explicit close()."""
        await self.connect()

    async def connect(self):
        async with self._lock():
            if self._terminal or self._connecting or self.is_connected:
                return
            self._connecting = True
            self._intentionally_closed = False
            try:
                headers = _handshake_headers(
                    self._api_key_id, self._api_secret, self._ws_path,
                    self._handle, self._wallet,
                )
                # websockets API drift: additional_headers (>=12) / extra_headers (older)
                try:
                    self.ws = await websockets.connect(
                        self._ws_url, additional_headers=headers,
                        ping_interval=self.PING_INTERVAL,
                        ping_timeout=self.PING_TIMEOUT,
                        close_timeout=self.CLOSE_TIMEOUT, open_timeout=15,
                    )
                except TypeError:
                    self.ws = await websockets.connect(
                        self._ws_url, extra_headers=headers,
                        ping_interval=self.PING_INTERVAL,
                        ping_timeout=self.PING_TIMEOUT,
                        close_timeout=self.CLOSE_TIMEOUT, open_timeout=15,
                    )

                # The reader is the SOLE consumer of the socket. Do NOT recv()
                # here — mixing a direct recv() with the reader's `async for` on
                # the same asyncio-API connection races and can silently starve
                # the reader. Start the reader first; it surfaces the welcome via
                # an Event, then connect() absorbs the welcome's limits.
                self._welcome = None
                self._welcome_event = asyncio.Event()
                if self._reader_task and not self._reader_task.done():
                    self._reader_task.cancel()
                self._reader_task = asyncio.create_task(self._reader_loop())
                try:
                    await asyncio.wait_for(self._welcome_event.wait(), timeout=15)
                except asyncio.TimeoutError:
                    if self._reader_task and not self._reader_task.done():
                        self._reader_task.cancel()
                    raise RuntimeError("no welcome within 15s")

                welcome = self._welcome or {}
                wdata = welcome.get("data", {}) or {}
                self.ring_buffer_size = wdata.get("ring_buffer_size")
                self.subscriptions_max = wdata.get("subscriptions_max")
                self.resolved_wallet = wdata.get("wallet")

                self._connected = True
                self._connecting = False
                self._reconnect_attempts = 0
                self.connect_count += 1
                self.last_message_at = time.time()

                # Resubscribe everything, carrying each scope's last_seq.
                for sub in list(self._subs.values()):
                    await self._send_subscribe(sub)

                # Start watchdog (reader already started above).
                if self._watchdog_task is None or self._watchdog_task.done():
                    self._watchdog_task = asyncio.create_task(self._watchdog_loop())

            except Exception as e:
                self._connecting = False
                # Auth rejected AT handshake (401 bad/revoked key or signature,
                # 403 insufficient scope): reconnecting with the same keys cannot
                # succeed, so go terminal immediately — same disposition as a 4013
                # close — rather than retry-hammering the full backoff budget.
                # Anything without an HTTP status (5xx, network, DNS, timeout) is
                # transient and falls through to the normal reconnect path.
                status = _handshake_status(e)
                if status in (401, 403):
                    self._terminal = True
                    self._on_terminal(status)
                    return
                await self._schedule_reconnect(reason=f"connect_failed: {e}")

    # ------------------------------------------------------------- subscribe
    async def subscribe(self, channel: str, scope_id: str,
                        last_seq: Optional[int] = None,
                        cadence_bearing: bool = False):
        """Subscribe (or re-register) a (channel, scope_id). `cadence_bearing`
        opts the sub into the data-staleness watchdog — set it for a running
        execution.state or an actively-trading account.state, leave it False
        for event-only/idle channels."""
        key = (channel, scope_id)
        sub = self._subs.get(key)
        if sub is None:
            sub = _Subscription(channel, scope_id, last_seq, cadence_bearing)
            self._subs[key] = sub
        else:
            sub.cadence_bearing = cadence_bearing
            if last_seq is not None:
                sub.last_seq = last_seq
        if self.is_connected:
            await self._send_subscribe(sub)
        # else: deferred — resubscribed automatically on next connect.

    async def _send_subscribe(self, sub: _Subscription):
        frame = {
            "command": "subscribe",
            "channel": sub.channel,
            "scope_id": sub.scope_id,
            "client_correlation_id": f"sub-{sub.channel}-{sub.scope_id}",
        }
        if sub.last_seq is not None:
            frame["last_seq"] = sub.last_seq
        await self.ws.send(json.dumps(frame))

    # --------------------------------------------------------------- command
    async def send_command(self, frame: dict, wait: float = 10.0) -> Optional[dict]:
        """Send a Tier-2/Tier-3 command and await its correlated result frame."""
        cci = frame.get("client_correlation_id") or uuid.uuid4().hex
        frame["client_correlation_id"] = cci
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[cci] = fut
        await self.ws.send(json.dumps(frame))
        try:
            return await asyncio.wait_for(fut, timeout=wait)
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending.pop(cci, None)

    # ---------------------------------------------------------- reader loop
    async def _reader_loop(self):
        try:
            while True:
                try:
                    raw = await asyncio.wait_for(self.ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                self.last_message_at = time.time()
                try:
                    frame = json.loads(raw)
                except Exception:
                    continue
                self._on_raw(frame)  # optional raw tap, fires for every frame

                # 0) Welcome — first frame of every connection. Surface it via the
                #    Event so connect() can absorb limits, then keep reading.
                if (self._welcome_event is not None
                        and not self._welcome_event.is_set()
                        and frame.get("channel") == "_meta"
                        and frame.get("event") == "welcome"):
                    self._welcome = frame
                    self._on_meta(frame)
                    self._welcome_event.set()
                    continue

                # 1) Resync result — server cannot satisfy a last_seq for a scope.
                #    Recovery is identical for all three reasons.
                if frame.get("result") == "resync":
                    await self._handle_resync(frame)
                    continue

                # 2) Command result — correlate to a pending send_command if its
                #    client_correlation_id matches; otherwise surface it (subscribe
                #    acks/errors carry NO cci and must not be silently dropped).
                if "result" in frame:
                    cci = frame.get("client_correlation_id")
                    fut = self._pending.get(cci) if cci else None
                    if fut and not fut.done():
                        fut.set_result(frame)
                    else:
                        self._on_command_result(frame)
                    continue

                # 3) _meta live transitions (welcome already consumed in connect()).
                if frame.get("channel") == "_meta":
                    self._handle_meta(frame)
                    continue

                # 4) Channel event — advance that scope's cursor, route by wallet.
                if "seq" in frame:
                    key = (frame.get("channel"), frame.get("scope_id"))
                    sub = self._subs.get(key)
                    if sub is not None:
                        sub.last_seq = frame["seq"]
                    self._on_event(frame)

        except websockets.ConnectionClosed as cc:
            code = getattr(cc, "code", None)
            if code is None:
                code = getattr(getattr(cc, "rcvd", None), "code", None)
            await self._on_close(code)
        except asyncio.CancelledError:
            return
        except Exception as e:
            await self._schedule_reconnect(reason=f"reader_error: {e}")

    async def _on_close(self, code: Optional[int]):
        self._connected = False
        # 4013 = credentials revoked — TERMINAL. Do not reconnect with these keys.
        if code == 4013:
            self._terminal = True
            self._on_terminal(code)
            return
        # Everything else (1001 going-away, 4015 heartbeat, 1011, etc.) reconnects.
        await self._schedule_reconnect(reason=f"closed: {code}")

    # ------------------------------------------------------ resync recovery
    async def _handle_resync(self, frame: dict):
        """All three resync reasons (buffer_overflow / last_seq_ahead_of_server /
        scope_not_found) recover identically: drop the cursor, REST-fetch current
        state, re-subscribe WITHOUT last_seq."""
        channel = frame.get("channel")
        scope_id = frame.get("scope_id")
        key = (channel, scope_id)
        sub = self._subs.get(key)
        if sub is None:
            return
        sub.last_seq = None  # clear cursor so the resubscribe hydrates fresh
        if self._rest_fetch_current_state is not None:
            try:
                await self._rest_fetch_current_state(channel, scope_id)
            except Exception:
                pass
        if self.is_connected:
            await self._send_subscribe(sub)

    # --------------------------------------------------------- _meta events
    def _handle_meta(self, frame: dict):
        """`_meta` reports server<->HL upstream health, NOT server<->client health.
        These are informational — surface them; DO NOT reconnect on them."""
        self._on_meta(frame)

    # ----------------------------------------------------------- watchdog
    async def _watchdog_loop(self):
        """Two-check watchdog. Layer 2 of liveness; layer 1 is the library
        ping/pong."""
        while not self._intentionally_closed and not self._terminal:
            try:
                await asyncio.sleep(self.WATCHDOG_INTERVAL)
                if self._intentionally_closed or self._terminal:
                    break

                # Check 1: reader task died but we still think we're connected
                # (transport alive, loop dead — the silent failure ping can't catch).
                if (self._reader_task is not None and self._reader_task.done()
                        and self._connected):
                    await self._force_reconnect("watchdog_dead_reader")
                    continue

                # Check 2: connected + cadence-bearing subs + no data past threshold.
                # Skipped entirely for idle/event-only subs (silence is normal).
                if (self._connected and self.last_message_at
                        and self._has_cadence_bearing_subs()):
                    age = time.time() - self.last_message_at
                    if age > self.DATA_STALE_THRESHOLD:
                        await self._force_reconnect(f"watchdog_stale_{age:.0f}s")
                        continue
            except asyncio.CancelledError:
                break
            except Exception:
                continue

    async def _force_reconnect(self, reason: str):
        self._connected = False
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
        self._reconnect_attempts = 0  # watchdog trip = fresh attempt budget
        await self.connect()

    # ---------------------------------------------------------- reconnect
    async def _schedule_reconnect(self, reason: str = ""):
        """Fire-and-forget reconnect as a SEPARATE task so the calling reader
        loop can finish and reader_task.done() == True before connect() inspects
        it (an inline await here can freeze the consumer)."""
        if self._intentionally_closed or self._terminal:
            return
        self._reconnect_attempts += 1
        if self._reconnect_attempts > self.MAX_RECONNECT_ATTEMPTS:
            # Exhausted — surface as terminal-ish; caller decides (alert, etc.).
            self._on_terminal(-1)
            return
        delay = min(self.RECONNECT_BASE_DELAY * (2 ** (self._reconnect_attempts - 1)), 30)
        asyncio.create_task(self._do_reconnect(delay))

    async def _do_reconnect(self, delay: float):
        try:
            await asyncio.sleep(delay)
            await self.connect()
        except Exception:
            if not self._intentionally_closed and not self._terminal:
                await asyncio.sleep(5)
                try:
                    await self.connect()
                except Exception:
                    pass

    # -------------------------------------------------------------- close
    async def close(self):
        """Clean, intentional shutdown. Stops the reconnect loop."""
        self._intentionally_closed = True
        if self._watchdog_task:
            self._watchdog_task.cancel()
        if self._reader_task:
            self._reader_task.cancel()
        if self.ws:
            try:
                await self.ws.close(code=1000)
            except Exception:
                pass
        self._connected = False


# --------------------------------------------------------------------------
# Example — stream a running execution with full auto-reconnect/resume.
# This is the seed for runnable example #1.
# --------------------------------------------------------------------------
async def _example(api_key_id: str, api_secret: str, handle: str,
                   wallet: str, execution_id: str):
    def on_event(frame):
        ev = frame.get("event")
        owner = (frame.get("data") or {}).get("wallet")  # route by data.wallet
        print(f"[{frame.get('channel')}/{ev}] seq={frame.get('seq')} wallet={owner}")

    def on_meta(frame):
        ev = frame.get("event")
        if ev == "welcome":
            c = (frame.get("data") or {}).get("connectivity", {})
            print(f"# welcome: ring={frame['data'].get('ring_buffer_size')} "
                  f"rest={c.get('rest', {}).get('status')} ws={c.get('ws', {}).get('status')}")
        else:
            # upstream_rest_degraded / _resumed / upstream_ws_* — info only, no reconnect.
            print(f"# _meta {ev}: {frame.get('data')}")

    def on_terminal(code):
        print(f"# TERMINAL (code={code}) — credentials revoked or reconnect exhausted; stopping")

    # Resync recovery: a real REST fetcher built from the cited channel->endpoint
    # mapping. On a resync result the client calls this to bridge the re-hydration
    # gap, then resubscribes fresh.
    rest_fetch_current_state = make_rest_fetcher(
        base_url="https://api.viperexecution.com",
        api_key_id=api_key_id, api_secret=api_secret, handle=handle, wallet=wallet,
    )

    client = ViperWSClient(
        api_key_id=api_key_id, api_secret=api_secret, handle=handle, wallet=wallet,
        on_event=on_event, on_meta=on_meta, on_terminal=on_terminal,
        rest_fetch_current_state=rest_fetch_current_state,
    )
    await client.start()
    # Running execution => cadence-bearing => data-staleness watchdog active.
    await client.subscribe("execution.state", execution_id, cadence_bearing=True)
    try:
        while not client._terminal:
            await asyncio.sleep(1)
    finally:
        await client.close()


if __name__ == "__main__":
    import os
    asyncio.run(_example(
        api_key_id=os.environ["VIPER_API_KEY"],
        api_secret=os.environ["VIPER_API_SECRET"],
        handle=os.environ.get("VIPER_HANDLE", ""),
        wallet=os.environ.get("VIPER_TEST_WALLET_A", ""),
        execution_id=os.environ.get("VIPER_EXEC_ID", ""),
    ))

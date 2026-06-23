"""Viper Execution REST client.

Async, typed-but-raw: every method returns the parsed JSON **dict** (raw access
always works); the typed signatures + `rest_types` TypedDicts are for editor and
type-checker support. Mirrors `ViperWSClient`'s construction model — explicit
constructor for your own secret store, or `from_env()` for the quick path.

Auth is HMAC-SHA256 over the canonical string `"{ts}{METHOD}{path}{body}"`
(hostname excluded), seconds-precision timestamp. The signature is taken over
the EXACT body string that is sent — see `_request`.

Construction:
    # explicit (Vault / AWS / your own secret store)
    rest = ViperRestClient(api_key_id="vk_...", api_secret="...",
                           handle="your-handle", wallet="0x...")
    # or from the environment
    rest = ViperRestClient.from_env()

    async with rest:                       # closes the http client on exit
        inst = await rest.instruments()
        res = await rest.execute(algo="glidemaker", symbol="BTC",
                                 side="buy", total_size=0.004,
                                 params={"strategy": "passive", "post_only": True})

You may inject your own `httpx.AsyncClient` (timeouts/proxies/pools) via
`http_client=...`; otherwise a default one is created and owned by the client.
"""
from __future__ import annotations

import os
import json
import time
import uuid
import hmac
import hashlib
import asyncio
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import httpx

from .exceptions import ViperError, exception_for

DEFAULT_BASE_URL = "https://api.viperexecution.com"
_MIN_MUTATING_INTERVAL = 1.1  # seconds between mutating calls (replay spacing)


class ViperRestClient:
    """Async REST client for the Viper v1 API."""

    def __init__(
        self,
        api_key_id: str,
        api_secret: str,
        *,
        handle: Optional[str] = None,
        wallet: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        self._api_key_id = api_key_id
        self._api_secret = api_secret
        self._handle = handle
        self._wallet = wallet.lower() if wallet else None
        self._base_url = base_url.rstrip("/")
        # Injected client is borrowed (caller owns it); a default one is owned.
        self._http = http_client or httpx.AsyncClient(timeout=30)
        self._owns_http = http_client is None
        # Per-instance mutating throttle state — NOT module-global (no shared
        # state across instances), so it can never deadlock a read.
        self._mutating_lock = asyncio.Lock()
        self._last_mutating = 0.0

    @classmethod
    def from_env(cls, **overrides) -> "ViperRestClient":
        """Construct from environment variables. Reads VIPER_API_KEY /
        VIPER_API_SECRET (required), VIPER_HANDLE / VIPER_WALLET / VIPER_REST_URL
        (optional). Explicit kwargs override the environment."""
        def _require(name: str) -> str:
            val = os.environ.get(name)
            if not val:
                raise RuntimeError(
                    f"{name} is not set. ViperRestClient.from_env() requires "
                    f"VIPER_API_KEY and VIPER_API_SECRET."
                )
            return val

        params: Dict[str, Any] = {
            "api_key_id": _require("VIPER_API_KEY"),
            "api_secret": _require("VIPER_API_SECRET"),
        }
        if os.environ.get("VIPER_HANDLE"):
            params["handle"] = os.environ["VIPER_HANDLE"]
        if os.environ.get("VIPER_WALLET"):
            params["wallet"] = os.environ["VIPER_WALLET"]
        if os.environ.get("VIPER_REST_URL"):
            params["base_url"] = os.environ["VIPER_REST_URL"]
        params.update(overrides)
        return cls(**params)

    # ----------------------------------------------------------------- context
    async def aclose(self) -> None:
        """Close the underlying http client if this instance owns it."""
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> "ViperRestClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ signing
    def _sign(self, method: str, path: str, body_str: str) -> Dict[str, str]:
        """Canonical: HMAC-SHA256(secret, "{ts}{METHOD}{path}{body}").
        `body_str` MUST be the exact string sent on the wire ("" for no body).
        Seconds-precision ts; server skew window is 600s past / 11s future."""
        ts = str(int(time.time()))
        payload = f"{ts}{method}{path}{body_str}"
        sig = hmac.new(self._api_secret.encode(), payload.encode(),
                       hashlib.sha256).hexdigest()
        headers = {
            "X-Viper-Api-Key-Id": self._api_key_id,
            "X-Viper-Signature": sig,
            "X-Viper-Timestamp": ts,
        }
        if self._handle:
            headers["X-Viper-Handle"] = self._handle
        if self._wallet:
            headers["Viper-Wallet"] = self._wallet  # no X- prefix (WS parity)
        return headers

    async def _throttle(self) -> None:
        """Enforce >= 1.1s spacing between mutating calls. Per-instance and
        mutating-only — reads never call this, so they're never blocked, and
        there is no nested lock acquisition, so it cannot deadlock."""
        async with self._mutating_lock:
            wait = _MIN_MUTATING_INTERVAL - (time.monotonic() - self._last_mutating)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_mutating = time.monotonic()

    # ------------------------------------------------------------------ request
    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[dict] = None,
        query: Optional[dict] = None,
        idempotency: bool = False,
        idempotency_key: Optional[str] = None,
        mutating: bool = False,
    ) -> Any:
        # GETs carry a cache-buster to dodge edge caching / same-second
        # idempotency collisions; the signature covers the full path incl. query.
        q = dict(query or {})
        if method == "GET":
            q["_n"] = uuid.uuid4().hex[:8]
        if q:
            path = f"{path}{'&' if '?' in path else '?'}{urlencode(q)}"

        # Serialize ONCE. The signature is over this exact string and the SAME
        # string is sent as the body — re-serializing between sign and send
        # would change the bytes and 401 every mutating call.
        body_str = json.dumps(body, separators=(",", ":")) if body is not None else ""
        headers = self._sign(method, path, body_str)
        if body is not None:
            headers["Content-Type"] = "application/json"
        if idempotency:
            headers["Idempotency-Key"] = idempotency_key or uuid.uuid4().hex

        if mutating:
            await self._throttle()

        # sign==send: never pass json=; content MUST be the exact signed string.
        resp = await self._http.request(
            method, f"{self._base_url}{path}", headers=headers,
            content=(body_str if body is not None else None),
        )
        return self._handle_response(resp)

    def _handle_response(self, resp: httpx.Response) -> Any:
        try:
            payload = resp.json()
        except Exception:
            payload = {"_raw": resp.text}

        if resp.status_code >= 400:
            retry_after = resp.headers.get("Retry-After")
            try:
                retry_after = float(retry_after) if retry_after is not None else None
            except ValueError:
                retry_after = None
            raise exception_for(resp.status_code, payload, retry_after=retry_after)
        return payload

    @staticmethod
    def _body(**kw) -> dict:
        """Drop None-valued kwargs so optional fields aren't sent as null."""
        return {k: v for k, v in kw.items() if v is not None}

    # ================================================================ READS
    async def health(self): return await self._request("GET", "/v1/health")
    async def status(self): return await self._request("GET", "/v1/status")
    async def connections(self): return await self._request("GET", "/v1/connections")
    async def limits(self): return await self._request("GET", "/v1/limits")
    async def check_limits(self): return await self._request("GET", "/v1/limits/check")

    # account (reads)
    async def account_state(self): return await self._request("GET", "/v1/account/state")
    async def balance(self): return await self._request("GET", "/v1/account/balance")
    async def account_type(self): return await self._request("GET", "/v1/account/type")
    async def fee_tier(self): return await self._request("GET", "/v1/account/fee-tier")
    async def builder_status(self): return await self._request("GET", "/v1/account/builder-status")
    async def fills(self, **query): return await self._request("GET", "/v1/account/fills", query=query)
    async def pnl(self, **query): return await self._request("GET", "/v1/account/pnl", query=query)
    async def settings(self): return await self._request("GET", "/v1/account/settings")
    async def referral_stats(self): return await self._request("GET", "/v1/account/referrals/stats")

    # positions (reads)
    async def positions(self): return await self._request("GET", "/v1/positions")
    async def entry_fees(self): return await self._request("GET", "/v1/positions/entry-fees")

    # executions (reads)
    async def executions(self, **query): return await self._request("GET", "/v1/executions", query=query)
    async def execution(self, execution_id: str): return await self._request("GET", f"/v1/executions/{execution_id}")
    async def execution_orders(self, execution_id: str): return await self._request("GET", f"/v1/executions/{execution_id}/orders")
    async def execution_logs(self, execution_id: str): return await self._request("GET", f"/v1/executions/{execution_id}/logs")
    async def execution_performance(self, execution_id: str): return await self._request("GET", f"/v1/executions/{execution_id}/performance")
    async def can_resume(self, execution_id: str): return await self._request("GET", f"/v1/executions/{execution_id}/can-resume")

    # orders (reads)
    async def orders(self, **query): return await self._request("GET", "/v1/orders", query=query)
    async def get_order(self, order_id: str): return await self._request("GET", f"/v1/orders/{order_id}")
    async def order_history(self, **query): return await self._request("GET", "/v1/orders/history", query=query)

    # market data (reads)
    async def instruments(self, **query): return await self._request("GET", "/v1/instruments", query=query)
    async def instrument(self, symbol: str): return await self._request("GET", f"/v1/instruments/{symbol}")
    async def validate(self, symbol: str, **query): return await self._request("GET", f"/v1/instruments/{symbol}/validate", query=query)
    async def price(self, symbol: str): return await self._request("GET", f"/v1/price/{symbol}")
    async def orderbook(self, symbol: str): return await self._request("GET", f"/v1/orderbook/{symbol}")
    async def market_stats(self, symbol: Optional[str] = None):
        path = f"/v1/markets/{symbol}/stats" if symbol else "/v1/markets/stats"
        return await self._request("GET", path)

    # leverage (read)
    async def leverage(self, symbol: str): return await self._request("GET", f"/v1/leverage/{symbol}")

    # preview is a read: nothing is placed, no Idempotency-Key, no throttle.
    async def preview(self, *, algo: str, symbol: str, side: str,
                      total_size: float, params: Optional[dict] = None, **extra):
        body = self._body(algo=algo, symbol=symbol, side=side,
                          total_size=total_size, params=params, **extra)
        return await self._request("POST", "/v1/execute/preview", body=body)

    # ============================================================ MUTATING [I]
    # Every method below carries idempotency=True + mutating=True (throttle).
    async def execute(self, *, algo: str, symbol: str, side: str,
                      total_size: float, params: Optional[dict] = None,
                      reduce_only: Optional[bool] = None,
                      post_only: Optional[bool] = None,
                      idempotency_key: Optional[str] = None, **extra):
        body = self._body(algo=algo, symbol=symbol, side=side,
                          total_size=total_size, params=params,
                          reduce_only=reduce_only, post_only=post_only, **extra)
        return await self._request("POST", "/v1/execute", body=body,
                                   idempotency=True, idempotency_key=idempotency_key,
                                   mutating=True)

    async def place_order(self, *, symbol: str, side: str, size: float,
                          order_type: str, price: Optional[float] = None,
                          idempotency_key: Optional[str] = None, **extra):
        body = self._body(symbol=symbol, side=side, size=size,
                          order_type=order_type, price=price, **extra)
        return await self._request("POST", "/v1/order", body=body,
                                   idempotency=True, idempotency_key=idempotency_key,
                                   mutating=True)

    async def cancel_order(self, *, symbol: str, order_id: int,
                           idempotency_key: Optional[str] = None):
        return await self._request("POST", "/v1/orders/cancel",
                                   body=self._body(symbol=symbol, order_id=order_id),
                                   idempotency=True, idempotency_key=idempotency_key,
                                   mutating=True)

    async def cancel_all(self, *, symbol: Optional[str] = None,
                         idempotency_key: Optional[str] = None):
        return await self._request("POST", "/v1/orders/cancel-all",
                                   body=self._body(symbol=symbol),
                                   idempotency=True, idempotency_key=idempotency_key,
                                   mutating=True)

    async def modify_order(self, *, idempotency_key: Optional[str] = None, **fields):
        return await self._request("POST", "/v1/orders/modify", body=self._body(**fields),
                                   idempotency=True, idempotency_key=idempotency_key,
                                   mutating=True)

    async def close_position(self, *, symbol: str, size: Optional[float] = None,
                             idempotency_key: Optional[str] = None):
        return await self._request("POST", "/v1/positions/close",
                                   body=self._body(symbol=symbol, size=size),
                                   idempotency=True, idempotency_key=idempotency_key,
                                   mutating=True)

    async def close_all(self, *, idempotency_key: Optional[str] = None):
        return await self._request("POST", "/v1/positions/close-all", body={},
                                   idempotency=True, idempotency_key=idempotency_key,
                                   mutating=True)

    async def cancel_execution(self, execution_id: str, *,
                               idempotency_key: Optional[str] = None):
        return await self._request("POST", f"/v1/executions/{execution_id}/cancel",
                                   body={}, idempotency=True,
                                   idempotency_key=idempotency_key, mutating=True)

    async def pause(self, execution_id: str, *, idempotency_key: Optional[str] = None):
        return await self._request("POST", f"/v1/executions/{execution_id}/pause",
                                   body={}, idempotency=True,
                                   idempotency_key=idempotency_key, mutating=True)

    async def resume(self, execution_id: str, *, idempotency_key: Optional[str] = None):
        return await self._request("POST", f"/v1/executions/{execution_id}/resume",
                                   body={}, idempotency=True,
                                   idempotency_key=idempotency_key, mutating=True)

    async def update_params(self, execution_id: str, *,
                            idempotency_key: Optional[str] = None, **fields):
        return await self._request("PATCH", f"/v1/executions/{execution_id}/params",
                                   body=self._body(**fields), idempotency=True,
                                   idempotency_key=idempotency_key, mutating=True)

    async def set_leverage(self, *, symbol: str, leverage: int,
                           is_cross: Optional[bool] = None,
                           idempotency_key: Optional[str] = None):
        return await self._request("POST", "/v1/leverage",
                                   body=self._body(symbol=symbol, leverage=leverage,
                                                   is_cross=is_cross),
                                   idempotency=True, idempotency_key=idempotency_key,
                                   mutating=True)

    async def update_settings(self, *, idempotency_key: Optional[str] = None, **fields):
        return await self._request("PUT", "/v1/account/settings", body=self._body(**fields),
                                   idempotency=True, idempotency_key=idempotency_key,
                                   mutating=True)

    async def nuke(self, *, confirm: bool, scope: Optional[str] = None,
                   symbol: Optional[str] = None,
                   idempotency_key: Optional[str] = None):
        """EMERGENCY: cancel all open orders then close all positions for the
        resolved wallet. `confirm=True` is REQUIRED (no default) — the API's only
        deliberate-action gate is the mandatory Idempotency-Key, and this client
        auto-fills that, so `confirm` restores the conscious yes-I-mean-it step.
        `scope` ("wallet"/"account") and `symbol` are functional params."""
        if confirm is not True:
            raise ValueError(
                "nuke() requires confirm=True — it cancels all orders and closes "
                "all positions for the resolved wallet."
            )
        return await self._request("POST", "/v1/nuke",
                                   body=self._body(scope=scope, symbol=symbol),
                                   idempotency=True, idempotency_key=idempotency_key,
                                   mutating=True)


__all__ = ["ViperRestClient"]

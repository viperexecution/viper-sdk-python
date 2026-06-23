"""
Shared launch flow for the live-algo examples (Glidemaker, Pacemaker).

NOT discovered as an example itself (leading underscore). The per-algo modules
import `run_algo` from here.

What run_algo does, in order:
  1. Read live BTC mark + sizing fields from one signed GET /v1/instruments.
  2. Size the order to a USD notional (default $250) — round DOWN to the
     instrument's sz_decimals, floored at its min order size.
  3. Print exactly what it is about to do and give a short Ctrl-C abort window.
     Nothing is sent during this window — abort here places no order.
  4. Connect over WS and fire ONE start_algo command (single-fire; an
     idempotency_key guards against an accidental double-send).
  5. Observe briefly (default 10s — a passive/TWAP algo may or may not fill in
     that window), then cancel the launched execution and close.

These examples place REAL orders on mainnet. There is no testnet.

Env:
  VIPER_API_KEY / VIPER_API_SECRET            required
  VIPER_HANDLE                                 optional
  VIPER_WALLET                                 required (wallet to trade)
  VIPER_EXAMPLE_USD        target notional     optional (default 250)
  VIPER_EXAMPLE_OBSERVE_S  observe seconds     optional (default 10)
  VIPER_EXAMPLE_NO_CANCEL  set to leave the execution running (default: cancel)
  VIPER_REST_URL / VIPER_WS_URL               optional (default mainnet)
"""
from __future__ import annotations

import os
import math
import time
import uuid
import hmac
import hashlib
import asyncio

import httpx

from viper import ViperWSClient

REST_BASE = os.environ.get("VIPER_REST_URL", "https://api.viperexecution.com")
TARGET_USD = float(os.environ.get("VIPER_EXAMPLE_USD", "250"))
OBSERVE_S = float(os.environ.get("VIPER_EXAMPLE_OBSERVE_S", "10"))
ABORT_S = 3
NO_CANCEL = bool(os.environ.get("VIPER_EXAMPLE_NO_CANCEL"))


def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise SystemExit(f"{name} is not set — required to run this example.")
    return val


def _signed_get(path_with_query: str) -> httpx.Response:
    """Signed GET. Signature is over the FULL path including the query string,
    per the gateway's HMAC contract. Lifted from the instruments family probe."""
    key = _require("VIPER_API_KEY")
    sec = _require("VIPER_API_SECRET")
    handle = os.environ.get("VIPER_HANDLE")
    ts = str(int(time.time()))  # seconds precision (ms -> 401)
    sig = hmac.new(sec.encode(), f"{ts}GET{path_with_query}".encode(),
                   hashlib.sha256).hexdigest()
    headers = {"X-Viper-Api-Key-Id": key, "X-Viper-Signature": sig,
               "X-Viper-Timestamp": ts}
    if handle:
        headers["X-Viper-Handle"] = handle
    return httpx.get(f"{REST_BASE}{path_with_query}", headers=headers, timeout=30)


def _fetch_btc_instrument() -> dict:
    """GET /v1/instruments, return the BTC perp record. Retries on 429."""
    for attempt in range(6):
        # cache-buster so a same-second retry isn't served a stale 429 edge cache
        path = f"/v1/instruments?_n={uuid.uuid4().hex[:8]}"
        r = _signed_get(path)
        if r.status_code != 429:
            break
        time.sleep(1.0 + attempt * 0.3)
    r.raise_for_status()
    items = r.json().get("items", [])
    for rec in items:
        if str(rec.get("symbol", "")).upper() == "BTC":
            return rec
    raise SystemExit("BTC instrument not found in /v1/instruments response.")


def _compute_size(target_usd: float, mark_price: float, sz_decimals: int,
                  min_size: float) -> float:
    """Round-DOWN sizing to clear a USD notional, floored at min_size.
    Lifted from probe_helpers.compute_size (size rounds down to sz_decimals)."""
    if mark_price <= 0:
        raise SystemExit(f"non-positive mark_price from API: {mark_price}")
    factor = 10 ** sz_decimals
    rounded = math.floor((target_usd / mark_price) * factor) / factor
    return max(rounded, min_size)


def _result_field(res: dict, *keys):
    data = (res or {}).get("data") or {}
    for k in keys:
        if k in data:
            return data[k]
        if k in (res or {}):
            return res[k]
    return None


async def run_algo(*, algo: str, side: str, params: dict, label: str,
                   client_correlation_id: str) -> int:
    """Size from live mark, disclose + abort window, fire once over WS, cancel."""
    wallet = _require("VIPER_WALLET").strip().lower()

    # 1) live mark + sizing — read-only, no order placed.
    inst = _fetch_btc_instrument()
    mark = float(inst["mark_price"])
    sz_decimals = int(inst["sz_decimals"])
    min_size = float(inst.get("min_order_value_size") or 0.0)
    size = _compute_size(TARGET_USD, mark, sz_decimals, min_size)
    notional = size * mark

    # 2) disclosure + abort window. Nothing is sent yet — Ctrl-C here is safe.
    print(f"# {label}: LIVE {side} {size} BTC "
          f"(~${notional:.0f} at mark {mark}) on MAINNET — this places a real order.")
    print(f"# Ctrl-C within {ABORT_S}s to abort (nothing sent yet)...")
    await asyncio.sleep(ABORT_S)  # KeyboardInterrupt during this aborts before any fire

    # 3) connect and fire exactly once.
    client = ViperWSClient.from_env(wallet=wallet)
    await client.start()
    try:
        frame = {
            "command": "start_algo",
            "client_correlation_id": client_correlation_id,
            "symbol": "BTC", "side": side, "total_size": size,
            "reduce_only": False, "algo": algo, "params": params,
            "idempotency_key": uuid.uuid4().hex,
        }
        res = await client.send_command(frame)
        if res is None:
            print(f"# {label}: no result within timeout — nothing confirmed launched.")
            return 1
        if "error" in res:
            err = res["error"]
            print(f"# {label}: REJECTED code={err.get('code')} "
                  f"details={err.get('details')}")
            return 1

        exec_id = _result_field(res, "execution_id", "id")
        print(f"# {label}: {res.get('result')} execution_id={exec_id} "
              f"status={_result_field(res, 'status')}")

        # 4) observe, then cancel (a passive/TWAP algo may or may not fill here).
        if exec_id and not NO_CANCEL:
            print(f"# observing {OBSERVE_S:.0f}s, then cancelling...")
            await asyncio.sleep(OBSERVE_S)
            cancel = await client.send_command({
                "command": "cancel_execution", "execution_id": exec_id,
                "idempotency_key": uuid.uuid4().hex,
                "client_correlation_id": f"cancel-{client_correlation_id}",
            })
            verdict = (cancel or {}).get("result") or (cancel or {}).get("error") or "(no ack)"
            print(f"# {label}: cancel -> {verdict}")
        elif exec_id and NO_CANCEL:
            print(f"# VIPER_EXAMPLE_NO_CANCEL set — leaving {exec_id} running.")
        return 0
    finally:
        await client.close()

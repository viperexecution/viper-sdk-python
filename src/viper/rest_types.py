"""Typed request/response hints for the REST client.

These are editor/type-checker hints, not a runtime wall. Every REST method
returns the parsed JSON **dict** as-is, so raw access always works:

    state = await client.account_state()
    equity = state["equity"]            # raw dict — never boxed

The TypedDicts below describe the shapes for callers who want autocomplete and
mypy coverage. All are `total=False`: REST responses have the same field
optionality the WS frames taught us (preview/control responses omit fields), so
the hints never over-promise presence. Treat them as documentation with teeth,
not guarantees.
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, TypedDict

Side = Literal["buy", "sell"]
Algo = Literal["pacemaker", "glidemaker", "ghostsweep", "flowscale", "flowband", "smart_exit"]
IndicatorType = Literal[
    "sma", "ema", "wma", "hma", "vwap", "bollinger", "keltner",
    "donchian", "rsi", "stochastic", "macd", "atr", "obv", "volma",
]


# ---- request bodies ----------------------------------------------------------

class ExecuteRequest(TypedDict, total=False):
    """POST /v1/execute. `params` is per-algo (discriminated on `algo`)."""
    algo: Algo
    symbol: str
    side: Side
    total_size: float
    params: Dict[str, Any]
    reduce_only: bool
    post_only: bool


class OrderRequest(TypedDict, total=False):
    """POST /v1/order — a single resting/marketable order."""
    symbol: str
    side: Side
    size: float
    order_type: str          # "limit" | "market" | ...
    price: float
    time_in_force: str
    post_only: bool
    reduce_only: bool
    client_order_id: str
    take_profit: float
    stop_loss: float


class IndicatorItem(TypedDict, total=False):
    """One entry in POST /v1/indicators/evaluate `indicators`. Params are
    registry-declared per type; omitted keys use server defaults (echoed back
    in `effective_params`)."""
    type: IndicatorType
    params: Dict[str, Any]


# ---- response shapes (illustrative; runtime value is always the raw dict) ----

class ExecutionResult(TypedDict, total=False):
    """Result of POST /v1/execute (and the start_algo command)."""
    execution_id: str
    algo: str
    symbol: str
    side: str
    total_size: float
    rounded_size: float
    status: str
    cloid_prefix: str
    started_at: str
    reduce_only: bool
    warnings: List[Any]
    notices: List[Any]


class InstrumentRecord(TypedDict, total=False):
    """One record from GET /v1/instruments (sizing fields used by the examples)."""
    symbol: str
    mark_price: float
    sz_decimals: int
    min_order_value: float
    min_order_value_size: float
    tick_size_at_mark: float
    max_price_sig_figs: int


class PriceResult(TypedDict, total=False):
    """GET /v1/price/{symbol} — live BBO."""
    symbol: str
    bid: float
    ask: float
    mid: float
    spread: float
    spread_bps: float


class Candle(TypedDict, total=False):
    """One bar from GET /v1/candles/{symbol}. `t` is the bar OPEN time in
    epoch seconds (UTC); the `candles` array carries closed bars only."""
    t: int
    o: float
    h: float
    l: float
    c: float
    v: float


class IndicatorResult(TypedDict, total=False):
    """One entry in POST /v1/indicators/evaluate `results`. `latest` maps each
    output series key (e.g. upper/mid/lower for bollinger) to its value on the
    evaluation bar — None while the indicator is still warming up."""
    type: str
    effective_params: Dict[str, Any]
    latest: Dict[str, Any]
    series: Dict[str, Any]


__all__ = [
    "Side", "Algo", "IndicatorType",
    "ExecuteRequest", "OrderRequest", "IndicatorItem",
    "ExecutionResult", "InstrumentRecord", "PriceResult",
    "Candle", "IndicatorResult",
]

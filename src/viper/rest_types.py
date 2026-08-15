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

from typing import Any, Dict, List, Literal, TypedDict, Union

Side = Literal["buy", "sell"]
Algo = Literal["pacemaker", "glidemaker", "ghostsweep", "flowscale", "flowband", "smart_exit"]
IndicatorType = Literal[
    "sma", "ema", "wma", "hma", "vwap", "bollinger", "keltner",
    "donchian", "rsi", "stochastic", "macd", "atr", "obv", "volma",
]
Interval = Literal["1m", "5m", "15m", "1h", "4h", "1d"]

#: Indicators whose value is a PRICE, so it can serve as a level. These are the
#: only ones accepted by ``at`` legs, by a level-``trail`` (no ``mult``), by
#: ``price_from`` and by ``indicator_level`` monitors. Oscillators, volume
#: series and ``atr`` are rejected 422 there -- "SL at RSI(60)" would be a $60
#: stop. ``offset`` legs accept every indicator, because the multiplier makes
#: any series usable as a distance.
PriceLevelIndicator = Literal[
    "sma", "ema", "wma", "hma", "vwap", "bollinger", "keltner", "donchian",
]


# ---- TP/SL composition legs --------------------------------------------------

class TpSlOffsetSpec(TypedDict, total=False):
    """``offset`` leg: trigger = anchor +/- ``mult`` x indicator value.

    Accepts EVERY registry indicator. Resolved at fill time (orders) or at
    terminal state (executions), anchored to the actual average fill price,
    and echoed back in full.
    """
    mult: float
    indicator: IndicatorType
    params: Dict[str, Any]
    interval: Interval
    series: str
    anchor: Literal["entry", "mark"]


class TpSlAtSpec(TypedDict, total=False):
    """``at`` leg: the trigger price IS the indicator's value on the last
    closed bar -- SL at the lower Bollinger, TP at the Donchian upper.

    ``indicator`` is restricted to the price-dimensioned set. Side-sanity is
    enforced at resolution: an SL resolving above a long's entry (or a TP
    below) is rejected 422 with the resolved value in the detail.
    """
    indicator: PriceLevelIndicator
    params: Dict[str, Any]
    interval: Interval
    series: str


class TpSlTrailSpec(TypedDict, total=False):
    """``trail`` leg, maintained by the exit engine on each closed bar.

    Three modes, and the valid ``indicator`` set differs between them:

    * distance-trail -- ``indicator`` + ``mult``; ``atr`` ONLY
    * level-trail    -- ``indicator``, no ``mult``; price-dimensioned only
    * percent-trail  -- ``pct``, no ``indicator``

    SL trails only ever tighten; TP trails follow the level both ways. A venue
    modify is issued only once the new trigger moves ``min_move_bps`` from the
    resting one, and the resting order's client_order_id survives modifies.
    """
    indicator: IndicatorType
    params: Dict[str, Any]
    interval: Interval
    series: str
    mult: float
    pct: float
    min_move_bps: float


class TpSlLeg(TypedDict, total=False):
    """One TP or SL leg -- **exactly one** of ``price`` / ``offset`` / ``at`` /
    ``trail``.

    TypedDict cannot express that exclusivity, so all four are declared
    optional here and the server enforces it (422 on zero or on more than one).
    """
    price: float
    offset: TpSlOffsetSpec
    at: TpSlAtSpec
    trail: TpSlTrailSpec


# ---- request bodies ----------------------------------------------------------

class ExecuteRequest(TypedDict, total=False):
    """POST /v1/execute. `params` is per-algo (discriminated on `algo`).

    ``take_profit`` / ``stop_loss`` arm when the execution reaches a terminal
    state with fills, anchored to the algo's average fill price and sized to
    the accumulated fill. All six algos accept them.
    """
    algo: Algo
    symbol: str
    side: Side
    total_size: float
    params: Dict[str, Any]
    reduce_only: bool
    post_only: bool
    take_profit: TpSlLeg
    stop_loss: TpSlLeg


class OrderRequest(TypedDict, total=False):
    """POST /v1/order — a single resting/marketable order.

    ``take_profit`` / ``stop_loss`` accept either a bare number (absolute
    trigger price, the legacy form) or a :class:`TpSlLeg`. On a resting or
    partially filled entry, ``offset`` and ``at`` legs register with the
    persistent fill-watcher and report ``queued=true``; on a filled entry they
    resolve immediately against the actual average fill price.

    ``price_from`` resolves the LIMIT price from an indicator once at
    submission and echoes ``price_from_resolution``. Limit orders only, and
    exactly one of ``price`` or ``price_from``.
    """
    symbol: str
    side: Side
    size: float
    order_type: str          # "limit" | "market" | ...
    price: float
    price_from: TpSlAtSpec
    time_in_force: str
    post_only: bool
    reduce_only: bool
    client_order_id: str
    take_profit: Union[float, TpSlLeg]
    stop_loss: Union[float, TpSlLeg]


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
    "Side", "Algo", "IndicatorType", "Interval", "PriceLevelIndicator",
    "TpSlOffsetSpec", "TpSlAtSpec", "TpSlTrailSpec", "TpSlLeg",
    "ExecuteRequest", "OrderRequest", "IndicatorItem",
    "ExecutionResult", "InstrumentRecord", "PriceResult",
    "Candle", "IndicatorResult",
]

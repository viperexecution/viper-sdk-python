"""Unit tests for candles() + evaluate_indicators(). Pure — no network.

Asserts the request-construction contract: path/query/body shape, the
None-dropping on optional params, and the preview-pattern properties on
evaluate_indicators (read-shaped POST: no Idempotency-Key, no throttle).
The transport is stubbed at `_http.request`; signing runs for real.
"""
from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit, parse_qs

from viper.rest import ViperRestClient


class _Resp:
    status_code = 200
    headers: dict = {}

    def json(self):
        return {"ok": True}


class _StubHTTP:
    def __init__(self):
        self.calls = []

    async def request(self, method, url, *, headers=None, content=None):
        self.calls.append({"method": method, "url": url,
                           "headers": headers or {}, "content": content})
        return _Resp()

    async def aclose(self):
        pass


def _client():
    # Real signature: (api_key_id, api_secret, *, ..., http_client) —
    # inject the stub through the designed injection point.
    return ViperRestClient("vk_test", "vs_test", http_client=_StubHTTP())


def _query(url):
    return parse_qs(urlsplit(url).query)


def test_candles_path_query_and_none_dropping():
    c = _client()
    asyncio.run(c.candles("BTC", interval="1h", limit=50))
    call = c._http.calls[0]
    assert call["method"] == "GET"
    assert urlsplit(call["url"]).path.endswith("/v1/candles/BTC")
    q = _query(call["url"])
    assert q["interval"] == ["1h"]
    assert q["limit"] == ["50"]
    assert "include_forming" not in q  # None dropped, no null-ish param sent
    assert call["content"] is None


def test_evaluate_indicators_is_read_shaped_post():
    c = _client()
    asyncio.run(c.evaluate_indicators(
        symbol="ETH", interval="1m",
        indicators=[{"type": "atr", "params": {"period": 14}}],
        depth=3,
    ))
    call = c._http.calls[0]
    assert call["method"] == "POST"
    assert urlsplit(call["url"]).path.endswith("/v1/indicators/evaluate")
    body = json.loads(call["content"])
    assert body == {"symbol": "ETH", "interval": "1m",
                    "indicators": [{"type": "atr", "params": {"period": 14}}],
                    "depth": 3}  # include_forming=None dropped
    # preview pattern: read-shaped POST carries no Idempotency-Key
    assert "Idempotency-Key" not in call["headers"]
    # and signs like every other request
    assert "X-Viper-Signature" in call["headers"]


def test_evaluate_indicators_skips_throttle():
    c = _client()
    hits = []

    async def spy():
        hits.append(1)

    c._throttle = spy
    asyncio.run(c.evaluate_indicators(
        symbol="BTC", interval="1h", indicators=[{"type": "obv"}]))
    assert hits == []  # mutating=False: the write-throttle never engages

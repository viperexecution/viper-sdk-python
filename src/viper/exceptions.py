"""Viper SDK exception hierarchy.

A single base (`ViperError`) so callers can `except ViperError` to catch anything
the SDK raises, with specific subclasses for the cases worth handling distinctly.
The WS client surfaces most conditions through callbacks (`on_terminal`,
`on_command_result`) rather than exceptions; these types are for the REST client
and for the few WS paths that raise.
"""

from __future__ import annotations
from typing import Optional


class ViperError(Exception):
    """Base class for all SDK errors."""


class ViperAuthError(ViperError):
    """Authentication/authorization failure (bad signature, revoked key, 401/403).

    On the WS channel, credential revocation arrives as a terminal close (4013)
    via `on_terminal`, not as this exception.
    """


class ViperConnectionError(ViperError):
    """Transport-level failure: handshake rejected, socket dropped, reconnect
    attempts exhausted."""


class ViperRateLimitError(ViperError):
    """Request rejected by a rate-limit gate (HTTP 429 / WS subscription cap)."""

    def __init__(self, message: str, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


class ViperAPIError(ViperError):
    """The API returned an error response. Carries status + server payload."""

    def __init__(self, message: str, status: Optional[int] = None,
                 payload: Optional[dict] = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


__all__ = [
    "ViperError",
    "ViperAuthError",
    "ViperConnectionError",
    "ViperRateLimitError",
    "ViperAPIError",
]

"""Viper SDK exception hierarchy.

A single base (`ViperError`) so callers can `except ViperError` to catch anything
the SDK raises, with specific subclasses for the cases worth handling distinctly.
The WS client surfaces most conditions through callbacks (`on_terminal`,
`on_command_result`) rather than exceptions; these types are raised by the REST
client and the few WS paths that raise.

Every exception carries the machine-readable `error.code` from the API envelope
(`.code`), the HTTP `.status`, and the raw `.payload`, so a caller can branch on
the exact condition — e.g. tell a state `conflict` from an `idempotency_mismatch`
(both `ViperConflictError`) by reading `exc.code`.
"""
from __future__ import annotations
from typing import Optional


class ViperError(Exception):
    """Base class for all SDK errors.

    Carries optional API-envelope context. All fields default to None so the
    bare `ViperError("message")` form used by the WS paths still works.
    """

    def __init__(self, message: str, *, status: Optional[int] = None,
                 payload: Optional[dict] = None, code: Optional[str] = None,
                 retry_after: Optional[float] = None):
        super().__init__(message)
        self.status = status
        self.payload = payload
        self.code = code
        self.retry_after = retry_after


class ViperAuthError(ViperError):
    """Authentication/authorization failure (bad signature, revoked key, 401/403,
    insufficient scope, tenancy denied).

    On the WS channel, credential revocation arrives as a terminal close (4013)
    via `on_terminal`, not as this exception.
    """


class ViperConnectionError(ViperError):
    """Transport-level failure: handshake rejected, socket dropped, reconnect
    attempts exhausted."""


class ViperRateLimitError(ViperError):
    """Request rejected by a rate-limit gate (HTTP 429 / WS subscription cap).
    `retry_after` carries the server's `Retry-After` (seconds) when present."""


class ViperAPIError(ViperError):
    """The API returned an error response not covered by a more specific class.
    Carries `.status` + server `.payload` + machine-readable `.code`."""


class ViperValidationError(ViperAPIError):
    """Request was malformed or failed validation (400/422):
    validation_error, bad_request, missing_field, invalid_field_type,
    payload_too_large, unsupported_media_type."""


class ViperConflictError(ViperAPIError):
    """State conflict (409): conflict, state_transition_forbidden, or
    idempotency_mismatch (a reused Idempotency-Key with a different body —
    a caller bug; distinguish via `.code`)."""


class ViperNotFoundError(ViperAPIError):
    """Resource or route not found (404): not_found, unknown_route,
    scope_not_found."""


# error.code -> exception class. Anchored on the ErrorCode enum in
# viper_v1_public.yaml. Anything not mapped falls back to ViperAPIError, with
# .code still readable.
_CODE_MAP = {
    # auth / scope
    "unauthorized": ViperAuthError,
    "insufficient_scope": ViperAuthError,
    "forbidden": ViperAuthError,
    "tenancy_denied": ViperAuthError,
    # rate limiting
    "rate_limited": ViperRateLimitError,
    "venue_rate_limit": ViperRateLimitError,
    # conflict
    "conflict": ViperConflictError,
    "idempotency_mismatch": ViperConflictError,
    "state_transition_forbidden": ViperConflictError,
    # validation
    "validation_error": ViperValidationError,
    "bad_request": ViperValidationError,
    "missing_field": ViperValidationError,
    "invalid_field_type": ViperValidationError,
    "payload_too_large": ViperValidationError,
    "unsupported_media_type": ViperValidationError,
    # not found
    "not_found": ViperNotFoundError,
    "unknown_route": ViperNotFoundError,
    "scope_not_found": ViperNotFoundError,
}


def exception_for(status: int, payload: Optional[dict],
                  retry_after: Optional[float] = None) -> ViperError:
    """Map an API error response to the right typed exception.

    Prefers the machine-readable `error.code` (the contract); falls back to the
    HTTP status class. `.code`/`.status`/`.payload` are always populated.
    """
    err = (payload or {}).get("error") or {}
    code = err.get("code")
    message = err.get("message") or f"HTTP {status}"

    cls = _CODE_MAP.get(code)
    if cls is None:
        if status == 429:
            cls = ViperRateLimitError
        elif status in (401, 403):
            cls = ViperAuthError
        elif status == 404:
            cls = ViperNotFoundError
        elif status == 409:
            cls = ViperConflictError
        elif 400 <= status < 500:
            cls = ViperValidationError
        else:
            cls = ViperAPIError

    return cls(message, status=status, payload=payload, code=code,
               retry_after=retry_after)


__all__ = [
    "ViperError",
    "ViperAuthError",
    "ViperConnectionError",
    "ViperRateLimitError",
    "ViperAPIError",
    "ViperValidationError",
    "ViperConflictError",
    "ViperNotFoundError",
    "exception_for",
]

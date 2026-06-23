"""Unit tests for handshake-rejection status extraction. Pure — no network.

`_handshake_status` decides whether a failed connect is a permanent auth
rejection (401/403 -> terminal) or a transient error (-> reconnect), so it must
read the HTTP status across `websockets` library versions.
"""

from viper.ws import _handshake_status


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


class _NewStyleInvalidStatus(Exception):
    """websockets >=11: InvalidStatus carries .response.status_code."""
    def __init__(self, status_code):
        super().__init__(f"server rejected: {status_code}")
        self.response = _Resp(status_code)


class _OldStyleInvalidStatusCode(Exception):
    """older websockets: InvalidStatusCode carries .status_code directly."""
    def __init__(self, status_code):
        super().__init__(f"server rejected: {status_code}")
        self.status_code = status_code


def test_new_style_response_status():
    assert _handshake_status(_NewStyleInvalidStatus(401)) == 401
    assert _handshake_status(_NewStyleInvalidStatus(403)) == 403


def test_old_style_status_code():
    assert _handshake_status(_OldStyleInvalidStatusCode(401)) == 401
    assert _handshake_status(_OldStyleInvalidStatusCode(403)) == 403


def test_transient_errors_have_no_status():
    # network/transport errors carry no HTTP status -> None -> reconnect path
    assert _handshake_status(ConnectionResetError("dropped")) is None
    assert _handshake_status(TimeoutError("open timeout")) is None
    assert _handshake_status(OSError("dns failure")) is None


def test_5xx_is_extracted_but_not_terminal():
    # 503 is read (so the caller sees it) but the caller only terminals on
    # 401/403; a 5xx falls through to reconnect.
    assert _handshake_status(_NewStyleInvalidStatus(503)) == 503


def test_non_int_status_is_none():
    class Weird(Exception):
        status_code = "nope"
    assert _handshake_status(Weird()) is None

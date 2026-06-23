"""Unit tests for the resync endpoint mapping. Pure — no network."""

from viper import resync_endpoint, RESYNC_ENDPOINTS


def test_wallet_scoped_channels():
    assert resync_endpoint("account.state", "0xabc") == "/v1/account/state"
    assert resync_endpoint("execution.list", "0xabc") == "/v1/executions"


def test_execution_id_scoped_channels():
    assert resync_endpoint("execution.state", "exec_1") == "/v1/executions/exec_1"
    # chart shares the execution-detail endpoint
    assert resync_endpoint("execution.chart", "exec_1") == "/v1/executions/exec_1"


def test_monitor_channels():
    assert resync_endpoint("monitor.event", "mon_1") == "/v1/monitors/mon_1"
    assert resync_endpoint("monitor.state_change", "mon_1") == "/v1/monitors/mon_1"
    assert resync_endpoint("monitor.alert", "mon_1") == "/v1/monitors/mon_1"
    # stats is the one distinct shape -> live-stats sub-resource
    assert resync_endpoint("monitor.stats", "mon_1") == "/v1/monitors/mon_1/live-stats"


def test_live_only_channels_have_no_resync_path():
    # _meta and basket.event have no ring buffer -> never emit resync.
    assert resync_endpoint("_meta", "_meta") is None
    assert resync_endpoint("basket.event", "grp_1") is None
    assert "_meta" not in RESYNC_ENDPOINTS
    assert "basket.event" not in RESYNC_ENDPOINTS


def test_unknown_channel_is_none():
    assert resync_endpoint("nonsense.channel", "x") is None

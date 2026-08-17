"""Bug 4: continue_live must be true or the driver becomes a zombie.

With ``continue_live: false`` the driver makes exactly one connection
attempt and never retries: BOTH recovery paths in driver_lidar.cc are gated
on continue_live -
  * the start-retry loop:   ``while (start_val != 0 && param_.continue_live && is_running_)``
  * the fatal-error reconnect: ``if (fatal_error_ && param_.continue_live)``
so a lidar that is unreachable at boot (or drops mid-run) leaves a running
but permanently deaf node.  v1.0.7-dual ships continue_live: true.
"""

import re

import pytest
import yaml

from conftest import CONFIG_YAML, TWO_CONFIG_YAML, FIX_REASON


def _continue_live_values(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [entry["lidar"]["continue_live"] for entry in data["lidars"]]


_CONFIG_VALUES = _continue_live_values(CONFIG_YAML)


@pytest.mark.xfail(
    not all(_CONFIG_VALUES),
    reason=(
        "config.yaml ships continue_live: false on origin/main => one "
        "connection attempt then zombie; flipped to true in v1.0.7-dual"
    ),
    strict=True,
)
def test_config_yaml_continue_live_true():
    """Auto-recovery requires continue_live: true for every lidar."""
    assert all(v is True for v in _CONFIG_VALUES), (
        f"continue_live values in config.yaml: {_CONFIG_VALUES}; false means "
        "no start retry and no fatal-error reconnect (zombie node)"
    )


def test_two_config_continue_live_true():
    """Dual config must also enable auto-recovery (both lidars)."""
    if not TWO_CONFIG_YAML.is_file():
        pytest.skip("two-config.yaml absent on this branch (pre-v1.0.7-dual)")
    values = _continue_live_values(TWO_CONFIG_YAML)
    assert len(values) >= 2 and all(v is True for v in values), (
        f"continue_live values in two-config.yaml: {values}"
    )


# --- source-level documentation of the zombie mechanism --------------------

def test_start_retry_loop_gated_on_continue_live(driver_src):
    """The connect-retry do/while loop only loops when continue_live is set."""
    assert re.search(
        r"while\s*\(\s*start_val\s*!=\s*0\s*&&\s*param_\.continue_live", driver_src
    ), (
        "expected the start-retry loop gate "
        "`while (start_val != 0 && param_.continue_live ...)` in driver_lidar.cc"
    )


def test_fatal_error_reconnect_gated_on_continue_live(driver_src):
    """The mid-run fatal-error reconnect is also gated on continue_live."""
    assert re.search(
        r"if\s*\(\s*fatal_error_\s*&&\s*param_\.continue_live\s*\)", driver_src
    ), (
        "expected the fatal-error reconnect gate "
        "`if (fatal_error_ && param_.continue_live)` in driver_lidar.cc"
    )

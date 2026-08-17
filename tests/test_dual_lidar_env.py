"""Bug 1: DUAL_LIDAR env-flag parsing in launch/start_with_config.py.

v1.0.5-dual shipped ``if os.environ.get('DUAL_LIDAR') is True or 1 or "1":``
which is ALWAYS true (it parses as ``(x is True) or 1 or "1"``), so every
dual image loaded two-config.yaml unconditionally - and since that file was
not installed (bug 2) the driver crash-looped on every dual unit regardless
of the env flag.  Fixed in v1.0.7-dual (4a4e5f2) with a real `_env_flag`
truthy-string parser plus an isfile fallback guard.
"""

import re

import pytest

from conftest import (
    FIX_REASON,
    LAUNCH_SCRIPT,
    extract_env_flag_callable,
    mirrored_env_flag,
    read_text,
)

ENV_FLAG = extract_env_flag_callable()
LAUNCH_SRC = read_text(LAUNCH_SCRIPT)

needs_dual_fix = pytest.mark.xfail(
    ENV_FLAG is None,
    reason=FIX_REASON,
    strict=True,
)


# --- source-level pins (must hold on every branch, fixed or not) -----------

def test_broken_always_true_pattern_absent():
    """The v1.0.5-dual always-true expression must never reappear.

    ``os.environ.get('DUAL_LIDAR') is True or 1 or "1"`` - the `or 1` makes
    the whole condition constant-true regardless of the environment.
    """
    assert re.search(r"is\s+True\s+or\s+1", LAUNCH_SRC) is None, (
        "start_with_config.py contains the v1.0.5-dual always-true "
        "`is True or 1 or \"1\"` condition (operator-precedence bug)"
    )
    # `env-var is True` is always False for strings; any such comparison is
    # the same bug class even without the `or 1` tail.
    assert re.search(r"environ\.get\([^)]*\)\s+is\s+True", LAUNCH_SRC) is None, (
        "start_with_config.py compares os.environ.get(...) `is True`; env "
        "values are strings, this is never true - same bug class as v1.0.5-dual"
    )


# --- fix-presence pins (strict-xfail until v1.0.7-dual merges to main) -----

@needs_dual_fix
def test_dual_lidar_env_is_honored():
    """The launcher must consult DUAL_LIDAR to pick single vs dual config."""
    assert "DUAL_LIDAR" in LAUNCH_SRC
    assert "two-config.yaml" in LAUNCH_SRC


@needs_dual_fix
def test_missing_two_config_does_not_crash_loop():
    """DUAL_LIDAR set but two-config.yaml absent must fall back, not crash.

    The v1.0.7-dual fix guards the dual path with an isfile/exists check so a
    dual-flagged unit on a single image degrades to config.yaml instead of
    crash-looping (the v1.0.5-dual failure mode).
    """
    assert re.search(r"(isfile|exists)\s*\(", LAUNCH_SRC), (
        "no os.path.isfile/exists guard around the two-config.yaml path"
    )


@needs_dual_fix
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(None, False, id="unset"),
        pytest.param("0", False, id="zero"),
        pytest.param("", False, id="empty"),
        pytest.param("false", False, id="false"),
        pytest.param("1", True, id="one"),
        pytest.param("true", True, id="true"),
    ],
)
def test_dual_lidar_truth_table(value, expected):
    """unset/'0'/''/'false' => single config; '1'/'true' => dual config."""
    assert ENV_FLAG is not None, "no env-parsing helper in start_with_config.py"
    if value is None:
        # helper reads os.environ with a '' default; unset behaves like ''
        import os
        os.environ.pop("DUAL_LIDAR", None)
        assert ENV_FLAG("DUAL_LIDAR") is expected
    else:
        import os
        os.environ["DUAL_LIDAR"] = value
        try:
            assert ENV_FLAG("DUAL_LIDAR") is expected
        finally:
            os.environ.pop("DUAL_LIDAR", None)
    # the extracted helper must agree with the documented reference semantics
    assert mirrored_env_flag(value) is expected

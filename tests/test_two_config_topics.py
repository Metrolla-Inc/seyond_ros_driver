"""Bug 3: two-config.yaml lidar-2 topic contract.

v1.0.5/v1.0.6-dual shipped ``frame_topic: /iv_point2`` (missing 's') for the
second lidar; downstream consumers subscribe to /iv_points2, so lidar-2 data
silently vanished the moment the unit attached.  Fixed in v1.0.7-dual
(e7f10f0).
"""

import pytest
import yaml

from conftest import FIX_REASON, TWO_CONFIG_YAML

EXPECTED_LIDAR2_TOPIC = "/iv_points2"
TYPO_TOPIC = "/iv_point2"


def _lidar_frame_topics():
    data = yaml.safe_load(TWO_CONFIG_YAML.read_text(encoding="utf-8"))
    return [entry["lidar"]["frame_topic"] for entry in data["lidars"]]


if TWO_CONFIG_YAML.is_file():
    _TOPICS = _lidar_frame_topics()
    _XFAIL = len(_TOPICS) < 2 or _TOPICS[1] == TYPO_TOPIC
    _REASON = (
        f"two-config.yaml lidar-2 frame_topic is {_TOPICS[1] if len(_TOPICS) > 1 else 'missing'} "
        f"(the v1.0.5-dual typo); fixed to {EXPECTED_LIDAR2_TOPIC} in v1.0.7-dual (e7f10f0)"
        if _XFAIL else ""
    )
else:
    _TOPICS = None
    _XFAIL = True
    _REASON = FIX_REASON


@pytest.mark.xfail(not TWO_CONFIG_YAML.is_file(), reason=FIX_REASON, strict=True)
def test_two_config_yaml_exists():
    assert TWO_CONFIG_YAML.is_file(), "config/two-config.yaml is absent"


@pytest.mark.xfail(_XFAIL, reason=_REASON or FIX_REASON, strict=True)
def test_lidar2_frame_topic_is_iv_points2():
    """The second lidar must publish on exactly /iv_points2."""
    assert TWO_CONFIG_YAML.is_file(), "config/two-config.yaml is absent"
    topics = _lidar_frame_topics()
    assert len(topics) >= 2, "two-config.yaml does not define two lidars"
    assert topics[1] != TYPO_TOPIC, (
        "lidar-2 frame_topic carries the v1.0.5-dual typo /iv_point2; "
        "downstream expects /iv_points2 - lidar-2 data silently vanishes"
    )
    assert topics[1] == EXPECTED_LIDAR2_TOPIC


def test_lidar1_frame_topic_unchanged():
    """Control: lidar-1 keeps the single-lidar topic /iv_points."""
    if not TWO_CONFIG_YAML.is_file():
        pytest.skip("two-config.yaml absent on this branch (pre-v1.0.7-dual)")
    topics = _lidar_frame_topics()
    assert topics[0] == "/iv_points"

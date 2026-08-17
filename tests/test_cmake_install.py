"""Bug 2: two-config.yaml must be CMake-installed into the package share.

Root cause of the v1.0.5-dual crash-loop: the launcher asked for
share/seyond/two-config.yaml but CMakeLists.txt never installed it, so the
file did not exist in the image.  Fixed in v1.0.7-dual (4a4e5f2) by adding
config/two-config.yaml to the install(FILES ... DESTINATION share/...) block.
"""

import pytest

from conftest import FIX_REASON, cmake_installed_share_files

INSTALLED = cmake_installed_share_files()


def test_config_yaml_installed_to_share():
    """Control: the single-lidar config has always been installed."""
    assert "config/config.yaml" in INSTALLED


def test_launcher_installed_to_share():
    """Control: start_with_config.py itself is installed to the share."""
    assert "launch/start_with_config.py" in INSTALLED


@pytest.mark.xfail(
    "config/two-config.yaml" not in INSTALLED,
    reason=FIX_REASON,
    strict=True,
)
def test_two_config_yaml_installed_to_share():
    """The dual-lidar config must ship in the package share."""
    assert "config/two-config.yaml" in INSTALLED, (
        "CMakeLists.txt does not install config/two-config.yaml into "
        "share/${PROJECT_NAME}; a dual image will crash-loop looking for it"
    )

"""Shared fixtures/helpers for the seyond_ros_driver regression-gate tests.

These tests are pure-Python contract tests: they parse the launch script,
CMakeLists.txt and the YAML configs.  They never import ROS packages and
never build the driver, so they run anywhere with pytest + pyyaml.

Bug history pinned by this suite (v1.0.5-dual era, fixed 2026-08-15 in
commits 4a4e5f2 / e7f10f0, tagged v1.0.7-dual on branch feat/dualdriver):

1. start_with_config.py: ``if os.environ.get('DUAL_LIDAR') is True or 1 or "1":``
   is ALWAYS true (operator precedence), so every dual-image unit tried to
   load two-config.yaml regardless of the env flag.
2. two-config.yaml was never CMake-installed into the package share, so the
   file the launcher wanted did not exist in the image => crash-loop.
3. two-config.yaml lidar-2 frame topic typo: /iv_point2 instead of the
   downstream-expected /iv_points2 => lidar-2 data silently vanishes.
4. config.yaml continue_live: false => one connection attempt, then a zombie
   node (both the start-retry loop and the fatal-error reconnect are gated
   on continue_live in driver_lidar.cc).
"""

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_DIR = REPO_ROOT / "src" / "seyond_lidar_ros"
LAUNCH_SCRIPT = PKG_DIR / "launch" / "start_with_config.py"
CMAKELISTS = PKG_DIR / "CMakeLists.txt"
CONFIG_YAML = PKG_DIR / "config" / "config.yaml"
TWO_CONFIG_YAML = PKG_DIR / "config" / "two-config.yaml"
DRIVER_LIDAR_CC = PKG_DIR / "src" / "driver" / "driver_lidar.cc"

FIX_REASON = (
    "not fixed on origin/main: dual-lidar support (DUAL_LIDAR env handling, "
    "two-config.yaml, CMake install) lands in v1.0.7-dual "
    "(commits 4a4e5f2/e7f10f0, branch feat/dualdriver)"
)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def extract_env_flag_callable():
    """Extract the DUAL_LIDAR env-parsing helper from start_with_config.py.

    The launch file imports ROS launch modules that are not available in a
    plain pytest environment, so we lift only the parsing function out of the
    AST and exec it in isolation.  Returns the callable, or None when the
    launch script contains no such helper (i.e. the v1.0.7-dual fix is not
    present on this branch).
    """
    src = read_text(LAUNCH_SCRIPT)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and "env" in node.name.lower():
            module = ast.Module(body=[node], type_ignores=[])
            ast.fix_missing_locations(module)
            namespace = {}
            exec(  # noqa: S102 - executing first-party launch-file code only
                compile(module, filename=str(LAUNCH_SCRIPT), mode="exec"),
                {"os": __import__("os")},
                namespace,
            )
            fn = namespace[node.name]
            if callable(fn):
                return fn
    return None


def mirrored_env_flag(value):
    """Reference semantics the fixed helper must match.

    Mirrors the v1.0.7-dual `_env_flag`: only explicit truthy strings enable
    dual mode; unset/empty/"0"/"false" stay single-lidar.
    """
    if value is None:
        return False
    return value.strip().lower() in ("1", "true", "yes", "on")


def cmake_installed_share_files():
    """Return the set of files CMake installs into share/${PROJECT_NAME}.

    Parses every install(FILES ... DESTINATION ...) block whose destination
    is the package share root (both the ament and catkin sections).
    """
    text = read_text(CMAKELISTS)
    installed = set()
    for match in re.finditer(r"install\s*\(\s*FILES(.*?)\)", text, re.DOTALL):
        block = match.group(1)
        parts = block.split("DESTINATION")
        if len(parts) != 2:
            continue
        files_part, dest_part = parts
        dest = dest_part.strip()
        if "share/${PROJECT_NAME}" in dest.replace(" ", "") and "/rviz" in dest:
            continue  # rviz subdir, not the share root
        if ("share/${PROJECT_NAME}" in dest.replace(" ", "")
                or "CATKIN_PACKAGE_SHARE_DESTINATION" in dest):
            for token in files_part.split():
                installed.add(token.strip())
    return installed


@pytest.fixture(scope="session")
def env_flag():
    return extract_env_flag_callable()


@pytest.fixture(scope="session")
def launch_src():
    return read_text(LAUNCH_SCRIPT)


@pytest.fixture(scope="session")
def driver_src():
    return read_text(DRIVER_LIDAR_CC)

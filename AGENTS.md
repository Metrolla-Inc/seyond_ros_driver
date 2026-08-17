# AGENTS.md — seyond_ros_driver

## What this repo is

The **Seyond (Innovusion) lidar ROS 2 driver**, packaged into Docker images
for Metrolla edge units:

- `src/seyond_lidar_ros/` — the vendor driver source (ROS 1 + ROS 2
  adapters, ROS 2 Jazzy is what we ship). `seyond_node` connects to the
  lidar over TCP/UDP (`lidar_ip`, default `172.168.1.10`) and publishes the
  pointcloud on `frame_topic` (default `/iv_points`).
- `src/seyond_lidar_ros/launch/start_with_config.py` — the launch entry the
  images use; it selects the YAML config that drives the node.
- `src/seyond_lidar_ros/config/config.yaml` — single-lidar config.
- `src/seyond_lidar_ros/config/two-config.yaml` — dual-lidar config
  (lidar-2 on `/iv_points2`). **Exists only from v1.0.7-dual /
  `feat/dualdriver`** — not on `main` yet.
- `Dockerfile` / `Dockerfile.deb` / `Dockerfile.unified` + entrypoints —
  image builds; the vendored SDK ships as
  `seyond-ros2-jazzy-*.deb` (+ a git submodule), which is why CI has **no
  colcon build job** (Phase-5 work; see TESTS.md).

## Single vs dual images, fleet usage

- Version tags: `v1.0.x` = single-lidar; `v1.0.x-dual` = dual-lidar image.
- Edge fleets (self-hosted **openBalena** at `ob.metrolla.com`, repo
  `balena-fleets`) pin the image via the **`DRIVER_VERSION`** fleet/device
  variable — bumping `DRIVER_VERSION` is how a unit moves driver versions.
- Dual behavior at runtime is selected by the **`DUAL_LIDAR`** env var on
  the device (`1`/`true` ⇒ `two-config.yaml`; unset/`0`/`""`/`false` ⇒
  `config.yaml`).
- History: `v1.0.5-dual`/`v1.0.6-dual` were broken in the field (crash-loop
  at niagra-bottling precise-doorway, stopgap patched in-container);
  `v1.0.7-dual` (commits `4a4e5f2`/`e7f10f0`, branch `feat/dualdriver`) is
  the repo fix. Registry blobs live in Cloudflare R2 — pushes are capped by
  a ~40 Mbps uplink; never run parallel/duplicate pushes.

## Gotchas (the bug classes this gate exists for)

1. **Always-true env checks.** `os.environ.get('X') is True or 1 or "1"`
   parses as `(x is True) or 1 or "1"` and is constant-true; and
   `environ.get(...) is True` alone is constant-FALSE (env values are
   strings). Either way the flag is not actually consulted. Parse env flags
   with an explicit truthy-string set (`{'1','true','yes','on'}`), and keep
   a source-level test asserting the broken pattern stays absent.
2. **Config files must be CMake-installed.** A config referenced by a
   launch file but missing from the `install(FILES … DESTINATION share/…)`
   block exists in the repo yet not in the image ⇒ crash-loop only at
   deploy time. Every new config needs an install rule AND a test.
3. **Topic-name typos are silent.** `/iv_point2` vs `/iv_points2` produces
   zero errors — data just never reaches downstream. Topic names are a
   contract with consumers; pin them in tests, don't eyeball them.
4. **`continue_live: false` = zombie driver.** Both recovery paths in
   `driver_lidar.cc` (the start-retry loop and the fatal-error reconnect)
   are gated on `continue_live`. False means one connection attempt ever;
   a lidar that is late to boot or drops mid-run leaves a running-but-deaf
   node that connectivity checks call healthy. Ship `true` on edges.

## Setup / running tests

CI uses Python 3.12; tests need only `pytest` and `pyyaml`:

```bash
python3 -m pip install pytest pyyaml
python3 -m pytest tests/ -v -ra
```

See **TESTS.md** for the full suite inventory, expected counts on `main`
vs `feat/dualdriver`, the strict-xfail policy, and the PASS/FAIL/SKIP/ABORT
outcome semantics.

## Rules for agents working here

- The regression-gate program is **strictly additive**: add tests/CI/docs;
  do not modify driver source or delete files.
- Fix-presence tests are **conditional strict xfails** keyed off the tree
  itself. When `feat/dualdriver` merges to `main` they flip to required-PASS
  automatically; if one then errors as XPASS-strict, delete its marker —
  never loosen the assertion.
- Verify, don't guess: never report a suite that could not run as healthy
  (ABORT ≠ PASS).

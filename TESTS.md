# TESTS.md — seyond_ros_driver suite inventory

This repo wraps the Seyond (Innovusion) lidar ROS 2 driver into the edge
images run by the openBalena fleets. The regression-gate suite here is
**pure-Python contract tests**: it parses `launch/start_with_config.py`,
`CMakeLists.txt`, and the YAML configs — it never imports ROS and never
builds the driver, so it runs anywhere with `pytest + pyyaml` in under a
second.

The suite pins the four v1.0.5-dual field bugs (fixed 2026-08-15 in commits
`4a4e5f2` / `e7f10f0`, tagged **v1.0.7-dual**, currently on branch
`feat/dualdriver`). **Those fixes are NOT on `main` yet**, so on `main` the
fix-presence tests are **strict xfail** — they will automatically flip to
required-PASS the moment `feat/dualdriver` merges (a strict xfail that
starts passing errors the run, forcing the marker's removal).

1. **Always-true launch condition** — `if os.environ.get('DUAL_LIDAR') is
   True or 1 or "1":` parses as `(x is True) or 1 or "1"` and is constant
   true, so every dual image loaded `two-config.yaml` regardless of the env
   flag ⇒ crash-loop on every dual unit (seen at niagra-bottling
   precise-doorway).
2. **two-config.yaml never CMake-installed** — the launcher asked for
   `share/seyond/two-config.yaml` but the image never shipped it (the crash
   the always-true bug exposed).
3. **Lidar-2 topic typo** — `/iv_point2` vs downstream-expected
   `/iv_points2`: lidar-2 data silently vanishes when the second unit
   attaches.
4. **`continue_live: false`** — one connection attempt, then a zombie: both
   the start-retry loop and the fatal-error reconnect in `driver_lidar.cc`
   are gated on `continue_live`.

CI: `.github/workflows/regression-gate.yml` — `unit` (Python 3.12, pytest +
pyyaml) and `manifest-lint` (YAML configs parse with required lidar keys +
`package.xml` parses). **Deliberately no colcon/driver build job**: the
driver links the vendored Seyond SDK (`.deb` / submodule, ROS 2 Jazzy
toolchain) which is not reasonable to stand up in a GitHub runner — a
containerized build-smoke gate is **Phase-5 work**.

## Suite inventory

| Suite | Layer | What it tests | Run command | Runtime | Requirements |
|---|---|---|---|---|---|
| `tests/test_dual_lidar_env.py` (9 tests) | unit, ROS-free | Bug 1: source-level pin that the always-true `is True or 1` pattern (and the `environ.get(...) is True` bug class) is absent; DUAL_LIDAR truth table (unset/`"0"`/`""`/`"false"` ⇒ single config, `"1"`/`"true"` ⇒ dual) against the `_env_flag` helper lifted out of the launch file via AST; missing-two-config fallback guard (no crash-loop) | `python3 -m pytest tests/test_dual_lidar_env.py -v` | < 1 s | Python 3.12, pytest |
| `tests/test_cmake_install.py` (3 tests) | unit, ROS-free | Bug 2: `config/two-config.yaml` present in an `install(FILES … DESTINATION share/…)` block; controls: `config.yaml` and `start_with_config.py` are installed | `python3 -m pytest tests/test_cmake_install.py -v` | < 1 s | Python 3.12, pytest |
| `tests/test_two_config_topics.py` (3 tests) | unit, ROS-free | Bug 3: `two-config.yaml` exists; lidar-2 `frame_topic` is exactly `/iv_points2` (and explicitly not the `/iv_point2` typo); control: lidar-1 stays `/iv_points` | `python3 -m pytest tests/test_two_config_topics.py -v` | < 1 s | Python 3.12, pytest, pyyaml |
| `tests/test_continue_live.py` (4 tests) | unit, ROS-free | Bug 4: `continue_live: true` in `config.yaml` (and `two-config.yaml` when present); source-level pins of BOTH recovery gates in `driver_lidar.cc` (start-retry loop + fatal-error reconnect), documenting the zombie mechanism | `python3 -m pytest tests/test_continue_live.py -v` | < 1 s | Python 3.12, pytest, pyyaml |

Run everything CI runs, locally:

```bash
python3 -m pytest tests/ -v -ra
```

Expected on `main` (pre-merge of v1.0.7-dual): **5 passed, 2 skipped,
12 xfailed**. Expected once `feat/dualdriver` merges: **19 passed** (the
xfail conditions self-clear; verified against `origin/feat/dualdriver`).

## Outcome semantics

| Outcome | Meaning | Effect |
|---|---|---|
| **PASS** | All assertions held. | Gate open. |
| **FAIL** | An assertion did not hold — a real regression (e.g. the always-true bug class reappearing, the topic typo coming back, `continue_live` flipped off). | **Blocks release.** The failure message names the field incident the assertion guards. |
| **SKIP** | A declared precondition was not met, with an explicit reason (here: `two-config.yaml` absent pre-v1.0.7-dual). | Always reported, **never silent**. |
| **ABORT** | Could-not-verify: the environment failed (interpreter, checkout, pip install) before assertions ran. | **Blocks like FAIL**, but is an *environment* failure — fix the environment, do not "fix" the code. |

Strict-xfail note: xfails in this suite are **conditional and strict** — the
condition is computed from the tree itself (fix present or not), so an
XFAIL here always means "known-missing fix, tracked to v1.0.7-dual", never
"mystery failure we silenced". The FAIL/ABORT split exists because of the
standing verify-don't-guess rule: a suite that could not run is unverified,
and unverified must never be reported as healthy.

## Where these tests run, and where the results go

**Stored** in this repo, beside the code they test — `tests/`. There is no central test tree: packages that are vendored into the `datablade` monorepo carry their tests with them, and the standalone repo stays the source of truth.

**Run** in these environments:

| Environment | What runs | When |
|---|---|---|
| Developer machine | Any suite in the inventory above | On demand — commands in this file and in `AGENTS.md` |
| CI — GitHub `ubuntu-latest` hosted runner | `unit (launch/config contract tests)`, `manifest-lint` | Every push and pull request to `main` and `feat/**` |

**Results** are stored by GitHub Actions — there is no separate test-results database:

| Output | Where | Retention |
|---|---|---|
| Full logs, per-test output, job timings | The Actions run page for this repo | 90 days (org default) |
| Job summaries | `$GITHUB_STEP_SUMMARY` on the run page | With the run |
| Test code and this inventory | This repo, in git | Permanent |

Results expire with the log retention and nothing aggregates history yet — flake rate, duration drift, or how long an xfail has been open. That is Phase 6 of the program, not built yet.

Org-wide map of every gated repo and environment: [`infra/REGRESSION-GATES.md`](https://github.com/Metrolla-Inc/infra/blob/master/REGRESSION-GATES.md).

## Adding a test

1. Put the file in `tests/`, named `test_*.py`.
2. Assert a value — never "it did not raise". If the behaviour you are pinning
   is a silent fallback, assert the fallback counter directly.
3. Add a row to the inventory above: what it asserts, its outcome rules, its
   runtime dependencies. **`manifest-lint` fails the build if you skip this.**
4. Run it: `python3 -m pytest tests/ -q`

Rules that apply to every test here:

- **No wall-clock or absolute monotonic timestamps** — `time.monotonic()` is
  seconds since boot, so a literal that works locally fails on a fresh runner.
  Always compute relative to a value captured inside the test.
- **No `sleep`, no network, no DDS, no database** in a unit test. Anything that
  needs those belongs in the replay smoke.
- **No dependence on ambient state** (a sourced workspace, a running node, a
  leftover file, another test). Tests must pass in any order.
- **Never `except: pass`** — swallowing the failure is how the original bug
  survived.
- **Frame-counted thresholds are rate-dependent**: a window in frames halves at
  10 Hz versus 5 Hz. Parametrise by rate.
- **A pass with zero rows processed is a failure** — assert the count.
- **Verify the test fails** when you break the behaviour deliberately, once,
  before opening the PR.

Pinning a bug you are not fixing yet — use a **strict xfail**, never a skip or a
deletion: `@pytest.mark.xfail(strict=True, reason="…")`. It reports every run and **turns red when the bug is fixed**,
which forces the test to be promoted. Record the reason in the inventory.

Changing a topic, message type or QoS? Update this repo's declaration under
`infra/contracts/decls/` in the same PR — the chain check validates it.

Full guidelines: [`infra/REGRESSION-GATES.md` §6](https://github.com/Metrolla-Inc/infra/blob/master/REGRESSION-GATES.md#6-writing-a-new-test--guidelines).

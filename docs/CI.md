# CI and build resources

Workflows run on the shared self-hosted runner (`[self-hosted, Linux, X64, vaulthalla, vps-ci]`),
like Vaulthalla and PDFium. Nothing runs on the host directly:

- Every step runs in a disposable rootless Podman container: `ci/run-ci` (byte-identical to
  Vaulthalla's; keep it in sync there) with `ci/Containerfile` (Ubuntu 24.04 pinned by digest = the
  `ubuntu-24.04-amd64` build profile: g++ 14, CMake, Ninja, Meson, libpq-dev, PostgreSQL 16 server
  binaries for throwaway test clusters, Debian tooling, gh, vl-release).
- Consumer and package tests run in stock `ubuntu:24.04` / `ubuntu:26.04` images
  (`ci/run-ci --image … --root`, then the test itself as an unprivileged user).
- PostgreSQL for tests is `tests/pg-sandbox`: a private cluster in a temporary directory, Unix
  socket only, deleted afterwards. No service, no port, no shared database.

## Bounds

| Control | Default | Where |
|---|---|---|
| Compile parallelism | `PQXX_JOBS=8` (the `vps` runner has 10 threads) | workflow `env` → `pqxx-sdk build` |
| Container memory | `CI_MEMORY=6g` | `ci/run-ci --memory` |
| One heavy build per runner user | `flock ~/.cache/libpqxx-ci/heavy-build.lock`, waits ≤ 90 min | `tools/ci-guard` |
| Free disk before heavy steps | 4 GiB at work tree, caches, Podman storage | `tools/ci-guard` (`PQXX_MIN_FREE_GIB`) |
| Priority | `nice 10`, `ionice -c2 -n7` | `tools/ci-guard` |
| Workflow concurrency | CI cancels superseded runs; releases queue; one Nexus Raw publication at a time | workflow `concurrency` |
| Job timeouts | 10–90 min | workflows |

Measured: the libpqxx compile including upstream tests takes about 70 s at `-j8` on the
development machine (18 threads) and 134 s at `-j4` on the `vps` runner (10 threads); upstream's
suite 3 s, `sdk-test` 15–20 s, each container case about 1 min (mostly `apt-get`). The release job logs the container's peak memory;
check it on the first runner runs. The lock is per project (PDFium has its own); if both build at
once on a small runner, point `PQXX_LOCK_FILE` at PDFium's lock to serialize them.

## Caches

| Cache | Location on the runner | Bound |
|---|---|---|
| ccache | `~/.cache/libpqxx-ci/ccache` → `/cache/ccache` (used when `CCACHE_DIR` is set) | `CCACHE_MAXSIZE=2G` |
| CI image | `localhost/libpqxx-ci:<hash>` | `ci/run-ci` removes superseded tags |
| Upstream archive | `release/build/downloads/` in the checkout | one archive, re-verified on use |

Expensive work is not repeated: the release compiles libpqxx once, and every later job reuses the
uploaded `release/dist` artifact (re-verified with `vlr checksums --verify`). The `.deb` repackages
that same archive instead of compiling again.

## Workflows

| Workflow | Trigger | Does |
|---|---|---|
| `ci.yml` | push to main, PRs from this repository | unit tests, `pqxx-sdk check`, `vlr check`, source verification; guarded build with upstream tests; inspect, consumer, acquisition and publication-rule tests |
| `release.yml` | `v*` tag, manual (`publish: dry-run` default) | docs/RELEASING.md |
| `upstream.yml` | weekly, manual | discover; candidate build/test; pull request with the report |

Fork pull requests never run on the self-hosted runner.

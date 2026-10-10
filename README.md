# Vaulthalla libpqxx SDK

A small binary-distribution pipeline for [libpqxx](https://github.com/jtv/libpqxx): it pins an
official upstream release, compiles it with upstream's own CMake + Ninja build as a **static
library**, and packages a **relocatable SDK** (`libpqxx.a`, matching headers, generated
`config.h`, pkg-config metadata, licenses, provenance manifest). The SDK is published to Nexus Raw
and consumed by Vaulthalla's native Meson build through a pinned, checksummed download. No fork,
no committed upstream source, no system-wide libpqxx anywhere.

| | |
|---|---|
| Upstream | libpqxx **8.0.2**, tag `8.0.2`, commit `92e7d64a3ee91abcecbd5182096a8d8d36de12c0`, archive sha256 `028c5fba…c4440` (`upstream.lock.json`) |
| Version | `8.0.2-1` = libpqxx 8.0.2, SDK revision 1 (vl-release `debian-upstream` policy): tag `v8.0.2-1`, package `libpqxx-vh-dev 8.0.2-1` |
| Build | upstream CMake 3.28 + Ninja, g++ 14.2 (Ubuntu 24.04), C++20, `-O3 -DNDEBUG`, PIC, `-march=x86-64 -mtune=generic`, hardening, no LTO, no patches |
| Consumers | `dependency('libpqxx-vh', method: 'pkg-config', static: true)`; libpqxx static, system `libpq.so.5` dynamic |
| APT (primary) | `libpqxx-vh-dev` on apt.vaulthalla.sh (namespaced; coexists with Ubuntu's `libpqxx-dev`) |
| Nexus Raw (optional) | `libpqxx/<upstream>-<rev>/<profile>/libpqxx-sdk.tar.zst` (+ `.sha256`, `manifest.json`) |

## Status

- **Released: `8.0.2-1`** (tag `v8.0.2-1`). `libpqxx-vh-dev 8.0.2-1` is published on
  `apt.vaulthalla.sh`, and the SDK archive, manifest and test record are attached to the GitHub
  release. Release CI built it once on the `vps` runner, ran upstream's 298 tests and the full
  consumer/container/package test matrix, published it, and verified the APT index by sha256.
- Nexus Raw publication of the SDK archive is optional and not enabled (docs/RELEASING.md).
- Vaulthalla's switch to libpqxx 8 (and to `libpqxx-vh-dev`) is in progress in its own
  repository; docs/CONSUMING.md lists what the trial compiles found.

## SDK layout

```
libpqxx-sdk-8.0.2-1-ubuntu-24.04-amd64/
├── include/pqxx/…                     110 upstream headers (byte-identical) + generated internal/config.h
├── lib/libpqxx.a                      static archive (PIC)
├── lib/pkgconfig/libpqxx-vh.pc        prefix=${pcfiledir}/../..; Libs: ${libdir}/libpqxx.a -l:libpq.so.5
├── licenses/libpqxx/{COPYING,AUTHORS}, licenses/NOTICE.md
└── manifest.json                      upstream provenance, build configuration, measured requirements,
                                       config.h macros, sha256 of every file
```

## Repository layout

| Path | What |
|---|---|
| `upstream.lock.json` | pinned upstream release: tag, commit, tree, archive URL/sha256/size (written by `pqxx-sdk pin`) |
| `profiles/*.toml` | build/compatibility profiles: target OS/glibc/arch/ABI, image, compiler, flags, libpq, consumers |
| `patches/` | upstream patches (none; policy in `patches/README.md`) |
| `tools/pqxx-sdk` | `discover`, `pin`, `fetch-source`, `build`, `inspect`, `version`, `check`, `publish`, `verify-published`, `deb-install` |
| `tools/libpqxx-sdk-fetch` | standalone consumer acquisition script (copy into consumers) |
| `tools/upstream-candidate`, `tools/release-rehearsal`, `tools/ci-guard` | upstream update, local release rehearsal, runner protection |
| `tests/` | `sdk-test` (consumer end-to-end), `fetch-test`, `publish-test` (mock Nexus), `deb-test`, `containers/run`, `pg-sandbox`, `consumer/` (Meson project), `unit/` |
| `debian/`, `release.toml`, `.release/` | the `libpqxx-vh-dev` package and the vl-release contract |
| `ci/` | `run-ci` (shared, verbatim from Vaulthalla) + `Containerfile` (= the ubuntu-24.04-amd64 build environment) |

Everything generated lives in the ignored, disposable `release/`:
`release/build/{downloads,source,compile,stage,sdk,deb,metadata}` and the release assets in
`release/dist/`.

## Everyday commands

```sh
./ci/run-ci tests/pg-sandbox tools/pqxx-sdk build --profile ubuntu-24.04-amd64 --tests
a=release/build/sdk/dist/libpqxx-sdk-8.0.2-1-ubuntu-24.04-amd64.tar.zst
./ci/run-ci tools/pqxx-sdk inspect "$a" --auto-sidecar --pinned-source
./ci/run-ci tests/pg-sandbox tests/sdk-test "$a"        # consumer end-to-end
./ci/run-ci tests/fetch-test "$a"                       # acquisition/cache behaviour
tests/containers/run "$a"                               # stock Ubuntu 24.04 / 26.04 consumers
python3 -m unittest discover -s tests/unit -t .
tools/release-rehearsal                                 # the whole release pipeline, nothing published
tools/pqxx-sdk discover                                 # newer stable upstream release?
```

## Documentation

- [docs/CONSUMING.md](docs/CONSUMING.md): acquiring the SDK and using it from Meson (Vaulthalla pattern).
- [docs/COMPATIBILITY.md](docs/COMPATIBILITY.md): the target profile, what was tested, the matrix policy.
- [docs/RELEASING.md](docs/RELEASING.md): versions, vl-release, APT publication, the optional Nexus Raw channel.
- [docs/MAINTENANCE.md](docs/MAINTENANCE.md): upstream updates, build decisions, patches.
- [docs/CI.md](docs/CI.md): runner isolation, resource bounds, workflows.

# Consuming the SDK (Vaulthalla integration pattern)

The SDK is a pinned, prebuilt dependency. A consumer fetches exactly one artifact *before* Meson
runs, verifies it, and points Meson at its private pkg-config directory. Nothing is installed
system-wide, no source or binary enters the consumer's Git history, and compilation never touches
the network.

This page describes the pattern; Vaulthalla's repository has not been changed.

## Option A: the APT package (the published channel)

```sh
sudo apt install libpqxx-vh-dev        # from apt.vaulthalla.sh (Vaulthalla's APT repository)
```

The package installs the SDK at `/usr/lib/x86_64-linux-gnu/libpqxx-vh/` and
`/usr/lib/x86_64-linux-gnu/pkgconfig/libpqxx-vh.pc`, so the Meson dependency below resolves with
no `pkg_config_path` and no download step. Pin it like any build dependency
(`libpqxx-vh-dev (= 8.0.2-1)` in `Build-Depends`, or an apt pin in CI images). It coexists with
Ubuntu's `libpqxx-dev`, and servers need only `libpq5`.

## Option B: the pinned SDK archive (no package installation)

For builds that must not install packages, the same files are published as a relocatable archive
(GitHub release asset now; Nexus Raw once enabled, docs/RELEASING.md) and fetched by a small script.

## 1. Add the fetch script and a pin

Copy `tools/libpqxx-sdk-fetch` unchanged (one file; Python ≥ 3.11 standard library + the `zstd`
program) into the consumer, e.g. `tools/libpqxx-sdk-fetch`, and track a pin:

```toml
# deps/libpqxx-sdk.toml — pinned libpqxx static SDK (https://github.com/vaulthalla/libpqxx).
# Change sdk_version, profile and sha256 together, deliberately.
sdk_version = "8.0.2-1"
profile = "ubuntu-24.04-amd64"
sha256 = "<from the release's libpqxx-sdk.tar.zst.sha256>"
base_url = "https://repo.valkyrianlabs.com/repository/cpp-sdk"
```

Generate a pin from what is published (reads `libpqxx-sdk.tar.zst.sha256`):

```sh
tools/libpqxx-sdk-fetch --make-pin 8.0.2-1 ubuntu-24.04-amd64 \
  --base-url https://repo.valkyrianlabs.com/repository/cpp-sdk > deps/libpqxx-sdk.toml
```

The pinned sha256 is the trust anchor: the server's own `.sha256` is never consulted for a normal
fetch, so a replaced artifact fails the build instead of being used.

## 2. Fetch before `meson setup`

```sh
pc="$(tools/libpqxx-sdk-fetch --pin deps/libpqxx-sdk.toml)"     # prints <sdk>/lib/pkgconfig
meson setup build -Dpkg_config_path="$pc"
# or: meson setup build $(tools/libpqxx-sdk-fetch --pin deps/libpqxx-sdk.toml --format meson)
```

`pkg_config_path` is stored in the build directory, so later `meson compile`/reconfigures need no
environment. The fetch script:

| Situation | Result |
|---|---|
| first use | download `libpqxx/<sdk_version>/<profile>/libpqxx-sdk.tar.zst`, check the pinned sha256, extract, check `manifest.json` (version, profile, every file's sha256), check host compatibility |
| cached | no network; re-checks the archive sha256 and `libpqxx.a`/`.pc` digests; a damaged extraction is re-extracted from the verified cached archive |
| `--offline` | cache only; exit 4 if absent |
| not published / unreachable / 401 | exit 4 with the URL |
| checksum or manifest mismatch | exit 3, nothing cached |
| incompatible host (OS, release older than the target, architecture, glibc, no `libpq.so.5`) | exit 1 (`--no-host-check` for cross builds) |

Cache: `$LIBPQXX_SDK_CACHE`, else `~/.cache/libpqxx-sdk`, or `--cache-dir`. Entries are keyed by
version, profile and sha256 and locked, so parallel builds share one download. In CI, mount it as
a persistent cache (`CI_CACHE_NAMES="… libpqxx-sdk"`, `LIBPQXX_SDK_CACHE=/cache/libpqxx-sdk`).

Credentials are needed only if the raw repository is not anonymously readable: use a
**read-only** account via `LIBPQXX_SDK_USER`/`LIBPQXX_SDK_PASSWORD` or `~/.netrc`. Consumers never
hold publishing credentials.

## 3. meson.build

```meson
pqxx_dep = dependency(
  'libpqxx-vh',
  method: 'pkg-config',
  static: true,
  required: true,
)
```

Sources keep `#include <pqxx/pqxx>`. The metadata (`lib/pkgconfig/libpqxx-vh.pc`):

```
prefix=${pcfiledir}/../..
Libs: ${libdir}/libpqxx.a -l:libpq.so.5
Cflags: -I${includedir}
```

- The archive is named **by path**, so neither a system `libpqxx.so` nor another `libpqxx.a` can be
  picked up by library search order.
- libpq is bound by its SONAME. `Requires: libpq`, `Requires.private: libpq` and `-lpq` all make
  Meson's `static: true` resolve `/usr/lib/<triplet>/libpq.a` (and fail on its private
  dependencies): measured with Meson 1.3.2, see docs/MAINTENANCE.md. `-l:libpq.so.5` always links
  the distribution's shared libpq, and needs only the `libpq5` runtime package.
- The `-I` path is the SDK's own include directory, which precedes `/usr/include`, so an installed
  `libpqxx-dev` is never seen (tests/sdk-test proves this from ninja's dependency records).

If the consumer uses libpq directly as well, it keeps its own `dependency('libpq')` (non-static).

## 4. Packaging the consumer

- Build-Depends: drop `libpqxx-dev`; add `zstd` (fetch) and `libpq5` (link). `libpq-dev` is not
  needed for libpqxx.
- Runtime: the executable needs only `libpq5` (`dh_shlibdeps` derives it from `DT_NEEDED`); no
  libpqxx package is installed on servers.
- Fetch in a step before the package build (e.g. vl-release `[debian] pre_build`) into a cache the
  build can read, and pass the pkg-config directory to `dh_auto_configure -- -Dpkg_config_path=…`.

## 5. Updating

A new SDK is a reviewed change to the pin (version + sha256 together). Nothing newer is ever
resolved implicitly; `sdk_version` names an immutable artifact.

## Before Vaulthalla switches

Trial compiles of Vaulthalla against the SDK (docs/MAINTENANCE.md, last section) found two things:

1. The `vhseed` target (`core/meson.build:176`) uses libpqxx without declaring the dependency and
   picks up `/usr/include/pqxx`. Add `dependencies: deps` to it first. Without a system
   libpqxx-dev it cannot compile, and with one it mixes headers.
2. libpqxx 8 changes row/field access (`row_ref`, `field_ref`): 111 compile errors in 17 files
   must be migrated before the 8.0.2 SDK can be used. A 7.10.7 SDK, built by the same tooling,
   works with today's sources unchanged, as an interim step.

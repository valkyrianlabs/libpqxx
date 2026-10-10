# Maintenance

## Upstream tracking

The only upstream input is `upstream.lock.json`: an official GitHub release of `jtv/libpqxx`, its
tag, commit, tree, and the tag archive's URL, size and sha256. Before any build,
`pqxx-sdk fetch-source` checks the archive's sha256 **and** the commit id that `git archive`
embeds in its pax header against the lock. The source is extracted fresh into
`release/build/source/` and never committed.

```sh
tools/pqxx-sdk discover            # pinned vs newest stable release (by version, not date)
tools/upstream-candidate [X.Y.Z]   # pin + VERSION + staged docs + build/tests + report (inside ./ci/run-ci)
```

Discovery ignores drafts, prereleases and non-`X.Y.Z` tags, and orders by version: upstream
publishes maintenance releases of older series after newer ones (7.10.7 appeared after 8.0.2), so
"most recently published" would be wrong. `upstream.yml` runs discovery weekly and opens a pull
request with the candidate report, which includes upstream's NEWS entry and the diff of upstream's
build files (`CMakeLists.txt`, `cmake/`, `requirements.json`, …) since the pinned release. Nothing
is published without a merged PR and a `vlr cut`.

Rebuilding an older release never fetches anything newer: check out the tag (or pin it with
`pqxx-sdk pin 8.0.1`), and the build uses exactly that lock.

## Build decisions (and why)

| Decision | Reason |
|---|---|
| Upstream CMake + Ninja, standard options only (`BUILD_SHARED_LIBS=OFF`, `CMAKE_POSITION_INDEPENDENT_CODE=ON`, `CMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF`, `CMAKE_CXX_STANDARD=20`, `BUILD_DOC/EXAMPLES/TOOLS=OFF`) | upstream-supported; no patches needed |
| `BUILD_TEST=ON` for release builds | upstream's 298-test suite runs against the static archive and a throwaway PostgreSQL before packaging |
| Ship `lib/libpqxx.a` (a regular file) | upstream installs `libpqxx-8.0.a` + a `libpqxx.a` symlink; the SDK has one unambiguous file |
| Drop upstream's `libpqxx.pc`, CMake package files, docs | they hardcode the install prefix and the generic name; the SDK has its own relocatable `libpqxx-vh.pc` |
| Do not ship `pqxx/config-compiler.h` | upstream's CMake install omits it (autotools installs it); it is a deprecated forwarder to `internal/config.h` |
| `-ffile-prefix-map` for source and build dirs | no build-machine paths in the archive (checked) and reproducible objects |
| Hardening flags explicit in the profile | Ubuntu's compiler defaults, made independent of the compiler's spec |
| Whole-archive link probe | proves PIC, the exact set of shared-library dependencies, and the highest symbol versions |
| `-l:libpq.so.5` in `Libs`, no `Requires` | see below |

### Why libpq is linked by SONAME

Measured with Meson 1.3.2 (Ubuntu 24.04) and `dependency(..., static: true)`: Meson runs
`pkg-config --static` and resolves `-lfoo` to `libfoo.a` when one exists. Ubuntu's `libpq-dev`
ships `libpq.a`, so `Requires: libpq`, `Requires.private: libpq` and plain `-lpq` all produced
`…/libpq.a …/libssl.a …/libcrypto.a -lpgcommon -lpgport …`. That links libpq statically (and fails
on its private dependencies). `-l:libpq.so.5` names the shared library's SONAME file, which Meson
passes through unchanged. It always binds the system's shared libpq, and consumers need only the
`libpq5` runtime package (libpqxx's headers do not include `libpq-fe.h`). Meson 1.3 prints a
harmless `Library 'libpq.so.5' not found for dependency 'libpqxx-vh', may not be successfully linked`
warning, because it only searches `-L` directories named in the `.pc`. The link is verified by the
tests (linker map, `DT_NEEDED`, `ldd`).

## Patches

None (`patches/README.md` has the policy). If one becomes necessary it is applied after source
verification, documented, recorded with its sha256 in the manifest and shipped under
`licenses/patches/`.

## Legacy fork (audited, not used)

`/srv/libpqxx-legacy/libpqxx-debian` packaged 7.10.4 as `libpqxx-dev` + `libpqxx-7.10` with
autotools: shared only, `CXXFLAGS="-O3 -D_GLIBCXX_USE_CXX11_ABI=1"`, which replaced dpkg's
hardening flags. The test suite was disabled. Its history went cmake → autotools and
static → shared. No source or CMake changes were made versus upstream 7.10.4; `debian/control`
had a malformed `Maintainer`. Nothing from it is needed here. Its package names collide with
Ubuntu's (`libpqxx-dev`); `libpqxx-vh-dev` does not collide with either.

## Vaulthalla and libpqxx 8 (trial compiles, not changes to Vaulthalla)

Vaulthalla 93fd99ad was compiled from a `git archive` copy. In that copy only its
`dependency('libpqxx')` line was changed, to the SDK's `dependency('libpqxx-vh', static: true)`.

1. **Build hygiene (needed for any SDK):** `core/meson.build:176`, the `vhseed` static library,
   has no `dependencies:` but includes libpqxx headers. Today it silently compiles against
   whatever `/usr/include/pqxx` the machine has. With the SDK, that mixed the host's legacy 7.10.4
   headers with the SDK archive and failed at link time (undefined `pqxx::argument_error` and
   related constructors). Adding `dependencies: deps` to that target fixes it.
2. **Against an 8.0.2 SDK:** configures and resolves `libpqxx-vh` 8.0.2, then fails to compile:
   **111 distinct error sites in 17 files**, nearly all in `core/src/db/query/*`. libpqxx 8
   iterates results as `pqxx::row_ref`/`pqxx::field_ref`, while Vaulthalla's model constructors
   take `pqxx::row const&`/`pqxx::field const&` (125 "no matching function", 83 reference
   initialisations). A further 13 sites in generic code need `.template as<T>()`. The migration is
   mechanical but must happen in Vaulthalla.
3. **Against a 7.10.7 SDK** (built with this tooling: `pqxx-sdk pin 7.10.7`, `VERSION` 7.10.7-1;
   238 upstream tests, inspection and the consumer test pass): with fix 1, Vaulthalla's unmodified
   sources build and link. `vaulthalla-server` contains libpqxx statically (282 `pqxx::`
   functions), the only libpq-related `DT_NEEDED` entry is `libpq.so.5`, and no header came from
   `/usr/include/pqxx`. That is a drop-in transitional SDK if the 8.x migration should not block
   leaving the legacy package.

With 7.10.x headers, GCC 14 `-O3 -Werror` reports the `-Wmaybe-uninitialized` false positive in
`<variant>` (via `pqxx::params`) that Vaulthalla's `compat/gcc_variant.hpp` works around; the
8.0.2 headers do not trigger it (the strict consumer test passes without any workaround).

Supporting older series needed two generalizations, now in the tooling. Generated headers are
detected rather than named: 7.x generates `pqxx/config-public-compiler.h`, 8.x
`pqxx/internal/config.h`. And `CMAKE_EXPORT_COMPILE_COMMANDS` is set explicitly, because 7.x does
not enable it.

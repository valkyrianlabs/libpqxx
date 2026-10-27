# Releasing

Releases use [vl-release](https://github.com/valkyrianlabs/vl-release) (`vlr` ≥ 0.5); `release.toml`
is the contract, `.github/workflows/release.yml` the CI transaction. vl-release owns versions,
staged release documentation (`.release/*_NEXT.md` → `RELEASE_NOTES.md`, `debian/changelog`), the
`.deb`, APT publication, GitHub releases and finalize. vl-release has no Nexus Raw support, so the
optional SDK-archive channel is `tools/pqxx-sdk publish`, which follows the same rules as
`vlr publish-deb`.

## Versions and artifact identity

The repository uses vl-release's `debian-upstream` version policy (`[version] policy`): `VERSION`
is `UPSTREAM-REVISION`, the upstream libpqxx release plus our packaging revision. One string
identifies a release everywhere:

| VERSION | Meaning | Tag | Debian package | Nexus Raw path (optional) |
|---|---|---|---|---|
| `8.0.2-1` | libpqxx 8.0.2, first SDK revision | `v8.0.2-1` | `libpqxx-vh-dev_8.0.2-1_amd64.deb` | `libpqxx/8.0.2-1/<profile>/` |
| `8.0.2-2` | same upstream, packaging-only change (`vlr cut revision`) | `v8.0.2-2` | `…_8.0.2-2_amd64.deb` | `libpqxx/8.0.2-2/<profile>/` |
| `8.0.3-1` | new upstream (`vlr version upstream 8.0.3`, then `vlr cut 8.0.3-1`) | `v8.0.3-1` | `…_8.0.3-1_amd64.deb` | `libpqxx/8.0.3-1/<profile>/` |

Versions are ordered like Debian versions (`8.0.2-10` > `8.0.2-9`). vl-release refuses
`patch|minor|major` (ambiguous), releases that go backwards, and a new upstream whose revision is
not 1. `tools/pqxx-sdk check` fails if `VERSION`'s upstream part and `upstream.lock.json` disagree.
Every published version is immutable; any change of bytes is a new revision.

## Channels

1. **APT (primary):** `libpqxx-vh-dev` on `https://apt.valkyrianlabs.com` (suite `stable`, component
   `main`, amd64), published with `vlr publish-deb`, exactly like Vaulthalla and PDFium. Since
   8.0.2-2; `https://apt.vaulthalla.sh` serves the same index, signed by the same Valkyrian Labs LLC
   key, so consumers configured for either keep working.
2. **GitHub release:** the `.deb`, the SDK archive (`libpqxx-sdk-<version>-<profile>.tar.zst`),
   its `.sha256`, manifest and consumer test record, and `SHA256SUMS`.
3. **Nexus Raw (optional, later):** the relocatable SDK archive for consumers that fetch it
   (`tools/libpqxx-sdk-fetch`, docs/CONSUMING.md). Runs only when the `NEXUS_RAW_REPO` variable is
   set, which needs a raw hosted repository that does not exist yet (below).

### The Debian package

`libpqxx-vh-dev` (Architecture amd64, Multi-Arch same, Depends `libpq5 (>= 13)`) installs the SDK
tree byte for byte at `/usr/lib/x86_64-linux-gnu/libpqxx-vh/`, plus
`/usr/lib/x86_64-linux-gnu/pkgconfig/libpqxx-vh.pc` with a fixed prefix. It ships no runtime
package, nothing in `/usr/include/pqxx`, no `libpqxx.so`/`.a` in the shared library directory, and
no Provides/Conflicts/Replaces. It installs next to Ubuntu's `libpqxx-dev` and the legacy fork's
packages (`tests/deb-test`). With it installed, `dependency('libpqxx-vh', static: true)` works
without `pkg_config_path`.

## Release transaction

```sh
vlr status                       # staged docs, version, policy
tools/release-rehearsal          # the whole pipeline locally (isolated copy), nothing published
vlr cut 8.0.2-1 --push           # first release; later: vlr cut revision --push, or vlr cut X.Y.Z-1 --push
```

The tag push starts `release.yml`. Upstream patch releases that pass `pqxx-sdk gate` are released by
`upstream.yml` itself (docs/MAINTENANCE.md, *Auto-release*): it merges the pull request, runs
`vlr cut X.Y.Z-1 --push --skip-tests` on the merge commit and dispatches this workflow on the tag with
`publish: publish` (a GITHUB_TOKEN tag push starts nothing, so there is one release run). Finalize
uses `RELEASE_FINALIZE_TOKEN` when set, else the job token (enough while `main` is unprotected).

The release run:

1. **release-check:** `vlr check --release`, `vlr version check`, `pqxx-sdk check`, unit tests.
2. **release-artifacts:** `vlr prepare`; `pqxx-sdk build --tests` (the only compile, upstream test
   suite included); `vlr build-deb` (repackages that SDK, collects the SDK assets into
   `release/dist`); `inspect`, `sdk-test` (writes the consumer test record), `fetch-test`,
   `publish-test`; container matrix and `.deb` test; `vlr checksums`, `vlr validate-artifacts`, the
   SDK publication gate; upload the artifact.
3. **publish-debian** (environment `Production`): `vlr publish-deb --require-enabled`: idempotent,
   refuses to replace published bytes, then waits until the APT index lists the package by sha256.
4. **publish-sdk** (only with `NEXUS_RAW_REPO`): `pqxx-sdk publish --require-enabled`.
5. **github-release:** `vlr github-release` attaches every release asset.
6. **finalize:** `vlr finalize` records `RELEASE_NOTES.md` and `debian/changelog` on `main`.

Re-runs are safe: identical published bytes are skipped and differing bytes refused at every step.
A manual run with `publish: dry-run` builds and validates everything without publishing.

### Repository configuration (as for Vaulthalla and PDFium)

| What | Where | Value |
|---|---|---|
| Self-hosted runner access | org runner group `Default` (selected repositories) | add `valkyrianlabs/libpqxx` |
| `NEXUS_USER`, `NEXUS_PASS` | `Production` environment **secrets** | the Nexus APT upload account (same as Vaulthalla's/PDFium's) |
| `NEXUS_REPO_URL` | `Production` environment variable | `https://apt.valkyrianlabs.com` |
| `RELEASE_PUBLISH_MODE` | `Production` environment variable | `nexus` |
| `RELEASE_FINALIZE_TOKEN` | org secret (exists) | finalize push to a protected `main` |
| `NEXUS_RAW_REPO` | variable, only for the optional Raw channel | e.g. `https://repo.valkyrianlabs.com/repository/cpp-sdk` |

The workflow maps `NEXUS_PASS` to vl-release's `NEXUS_PASSWORD` and `NEXUS_REPO_URL` to
`NEXUS_APT_REPO`, as Vaulthalla's and PDFium's do.

## Nexus Raw (optional)

Layout per SDK version and profile (all immutable):

```
libpqxx/8.0.2-1/ubuntu-24.04-amd64/libpqxx-sdk.tar.zst          the SDK
libpqxx/8.0.2-1/ubuntu-24.04-amd64/libpqxx-sdk.tar.zst.sha256   "<sha256>  libpqxx-sdk.tar.zst"
libpqxx/8.0.2-1/ubuntu-24.04-amd64/manifest.json                manifest + {archive: sha256, size}
```

`manifest.json` carries the upstream provenance (repository, tag, commit, tree, archive URL and
sha256), target OS/arch/ABI, compiler and exact package versions, the CMake arguments and one
complete compile command, measured symbol-version and libpq requirements, the generated config
macros, the upstream test result and the sha256 of every SDK file.

To enable it (needs approval; not done): create a raw hosted repository `cpp-sdk` with
**Deployment policy: Disable redeploy**, give the CI account `add`+`read` on it only, allow
consumers anonymous or read-only access, and set `NEXUS_RAW_REPO`. Nexus currently has no raw
repository (only maven, nuget, apt and npm), so nothing can be reused.

Publication rules (`tools/pqxx-sdk publish`, exercised by `tests/publish-test` against a mock
Nexus): every archive must pass `inspect` and have a passing consumer test record **for those
exact bytes**; the target must be an existing raw *hosted* repository; per file absent → upload,
identical sha256 → skip, different → refuse (exit 3); every file is downloaded again and hashed;
credentials go to curl on stdin, never argv; `RELEASE_PUBLISH_MODE` gates uploads.

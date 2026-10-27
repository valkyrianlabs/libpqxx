<!-- vl-release:generated-local -- regenerate with `vlr install-local-skill` after changing release.toml; local edits are overwritten -->
# Vaulthalla libpqxx SDK: release specifics

Generated from this repository's `release.toml`. The general workflow is in `SKILL.md`;
this file says how it applies here.

## Files

| Purpose | Path |
|---|---|
| Release contract | `release.toml` |
| Staged changelog (keep current) | `.release/CHANGELOG_NEXT.md` |
| Staged release notes (keep current) | `.release/RELEASE_NOTES_NEXT.md` |
| Published release notes (do not edit) | `RELEASE_NOTES.md` |
| Published Debian changelog (do not edit) | `debian/changelog` |

## Versions

- Canonical: `VERSION`
- Kept in sync by `vlr version …`: no other files
- Tags: `v8.0.2-1` on branch `main`; GitHub release title: `v8.0.2-1 — <title>`
- Version policy `debian-upstream`: the version is `UPSTREAM-REVISION` (e.g. `8.0.2-1`), ordered like Debian versions; it is also the Debian package version.
  - packaging-only change: `vlr version bump revision` / `vlr cut revision` (`8.0.2-1` -> `8.0.2-2`)
  - new upstream release: `vlr version upstream X.Y.Z` (revision restarts at 1), then `vlr cut X.Y.Z-1`
  - never `patch|minor|major` (refused as ambiguous)

## Release channels

- **Debian packages** (built by `vlr build-deb` from `debian/`, revision 1):
  - `libpqxx-vh-dev`: architecture `amd64`; must ship `usr/lib/x86_64-linux-gnu/libpqxx-vh/lib/libpqxx.a`, `usr/lib/x86_64-linux-gnu/libpqxx-vh/lib/pkgconfig/libpqxx-vh.pc`, `usr/lib/x86_64-linux-gnu/libpqxx-vh/include/pqxx/pqxx`, `usr/lib/x86_64-linux-gnu/libpqxx-vh/manifest.json`, `usr/lib/x86_64-linux-gnu/libpqxx-vh/licenses/libpqxx/COPYING`, `usr/lib/x86_64-linux-gnu/pkgconfig/libpqxx-vh.pc`, `usr/share/doc/libpqxx-vh-dev/copyright`; must never ship `usr/include/*`, `usr/lib/x86_64-linux-gnu/libpqxx.*`, `usr/lib/x86_64-linux-gnu/libpqxx-[0-9]*`, `usr/lib/x86_64-linux-gnu/pkgconfig/libpqxx.pc`, `usr/lib/x86_64-linux-gnu/cmake/*`, `*.so`, `*.so.*`
- **APT**: https://apt.valkyrianlabs.com (suite `stable`, components main), published by release CI with `vlr publish-deb`

## Before considering substantial work complete

```sh
vlr check
python3 -m unittest discover -s tests/unit -t .
```

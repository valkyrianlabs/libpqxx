# Vaulthalla libpqxx SDK — notes for agents

Binary distribution of upstream libpqxx as a relocatable static SDK (Nexus Raw), built with
upstream's CMake + Ninja. **Never** commit upstream sources or build output (everything generated
lives in the ignored `release/`), never fork or patch upstream without a documented reason
(`patches/README.md`), and never install libpqxx system-wide.

- Overview and layout: `README.md`. Consumers: `docs/CONSUMING.md`. Targets: `docs/COMPATIBILITY.md`.
- Updating upstream: `docs/MAINTENANCE.md`; start with `./ci/run-ci tools/upstream-candidate`.
- Releases, versions (`VERSION` 8.0.2-1 = libpqxx 8.0.2, SDK revision 1; vl-release `debian-upstream`
  policy), APT, optional Nexus Raw:
  `docs/RELEASING.md`; vl-release skill in `.claude/skills/vl-release/`.
- CI, resource bounds: `docs/CI.md`.

Rules of thumb:

- Build and test inside `./ci/run-ci` (the image is the build profile); PostgreSQL via
  `tests/pg-sandbox`. Do not install packages on the host.
- Never edit `upstream.lock.json` by hand (`tools/pqxx-sdk pin`), nor `debian/changelog` /
  `RELEASE_NOTES.md` (vl-release). Keep `.release/*_NEXT.md` current with material changes.
- `tools/libpqxx-sdk-fetch` is copied into consumers: keep it standalone (stdlib only) and
  backwards compatible with existing pin files.
- Before finishing: `python3 -m unittest discover -s tests/unit -t .`, `tools/pqxx-sdk check`,
  `vlr check`; for build or packaging changes `tools/release-rehearsal`.
- Do not publish, push tags, or create/modify Nexus repositories without the maintainer's approval.

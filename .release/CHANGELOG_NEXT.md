<!--
vl-release staged changelog: package-facing changes for the NEXT release.
Maintained continuously while working; `vlr prepare` renders it into debian/changelog
and resets this file to this template.

Format: one "- " bullet per change (concise, technical). Indent continuation lines.
Optional "## Section" headings group bullets; if used, every bullet must be under one.
One level of nested "  - " detail bullets is allowed. Consolidate; don't paste commit logs.
-->
- Package libpqxx 8.0.2 (tag 8.0.2, commit 92e7d64a3ee9) as a precompiled static SDK for
  ubuntu-24.04-amd64, built with upstream CMake + Ninja from the official release archive.
  - g++ 14, C++20, -O3, PIC, generic x86-64, hardening flags, no LTO, no patches.
  - Upstream's test suite runs against a throwaway PostgreSQL before packaging.
- New development-only package libpqxx-vh-dev: the SDK below /usr/lib/<multiarch>/libpqxx-vh
  and pkg-config metadata libpqxx-vh. No runtime package; coexists with Ubuntu's libpqxx-dev.
- pkg-config links the packaged libpqxx.a by path and libpq by SONAME (-l:libpq.so.5), so
  Meson static dependencies never bundle libpq.
- Versions are UPSTREAM-REVISION (vl-release debian-upstream policy): 8.0.2-1.

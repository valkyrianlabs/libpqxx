<!--
vl-release staged release notes: the user-facing story of the NEXT release.
Maintained continuously while working; `vlr prepare` promotes it into the release notes
history (and the GitHub release) and resets this file to this template.

Format: the first line is "# <release title>" WITHOUT a version number (vlr adds it).
Everything after the title is the Markdown release body. Describe the resulting behavior
for users and operators; keep it representative of what actually ships.
-->
# libpqxx 8.0.2 static SDK

The first release of the Vaulthalla libpqxx SDK: official upstream libpqxx **8.0.2** (tag `8.0.2`,
commit `92e7d64a3ee9`), precompiled as a static library for **Ubuntu 24.04 amd64**, so
applications link it without compiling libpqxx and without a system-wide libpqxx runtime package.
`8.0.2-1` is upstream 8.0.2, SDK revision 1.

- **APT:** `apt install libpqxx-vh-dev` from `apt.vaulthalla.sh`. It installs the SDK below
  `/usr/lib/x86_64-linux-gnu/libpqxx-vh` and the pkg-config module `libpqxx-vh`, without
  conflicting with Ubuntu's `libpqxx-dev`.
- **Meson:** `dependency('libpqxx-vh', method: 'pkg-config', static: true)`; sources keep
  `#include <pqxx/pqxx>`.
- **Linkage:** libpqxx is linked statically from the SDK archive; libpq stays the system's shared
  `libpq.so.5` (package `libpq5`).
- **Relocatable SDK archive:** `libpqxx-sdk-8.0.2-1-ubuntu-24.04-amd64.tar.zst` (attached to this
  release, with its sha256 and `manifest.json`: upstream provenance, build configuration,
  compatibility requirements) for consumers that fetch a pinned SDK instead of installing a package.
- **Compatibility:** built with g++ 14 (C++20, libstdc++ C++11 ABI) on Ubuntu 24.04; tested with
  C++20 and C++23 consumers on Ubuntu 24.04 and 26.04 LTS, and next to Ubuntu's libpqxx-dev.

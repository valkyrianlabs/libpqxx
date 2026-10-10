# Compatibility profile and target matrix

A static C++ archive is only compatible with consumers that agree on the platform, the C/C++
runtime and the compiler's view of the headers. Each SDK artifact therefore has exactly one
**profile** (`profiles/<id>.toml`), which is part of its identity and Nexus path, and its
`manifest.json` records what the build actually measured.

## ubuntu-24.04-amd64 (supported)

| Aspect | Value | Source |
|---|---|---|
| OS baseline | Ubuntu 24.04 LTS (noble); newer releases allowed, older refused by the fetch script | profile `target` |
| glibc | built against 2.39; the archive needs **GLIBC_2.32** at most (measured), consumers ≤ 2.34 in tests | manifest `requirements.glibc` |
| Architecture / ABI | amd64, x86-64 System V LP64, ELF; ISA baseline `-march=x86-64 -mtune=generic` (no v2/v3, no `native`) | profile `target`, `build.cxx_flags` |
| Compiler | GCC, `g++-14` 14.2.0-4ubuntu2~24.04.1 | manifest `build.environment` |
| C++ standard | built as C++20; consumers C++20 and C++23 tested | profile `consumers.cxx_std` |
| Standard library | libstdc++, C++11 ABI (`_GLIBCXX_USE_CXX11_ABI=1`, `__cxx11` symbols verified); needs **GLIBCXX_3.4.31**, **CXXABI_1.3.15** | manifest `requirements` |
| Code generation | `-O3 -DNDEBUG` (upstream Release), PIC, `-fstack-protector-strong -fstack-clash-protection -fcf-protection`, no LTO, no debug info | manifest `build.compile_command` |
| libpq | dynamic `libpq.so.5` (package `libpq5`); upstream minimum 13.0; the 77 libpq functions used exist since libpq5 10; built against 16.15 headers | manifest `requirements.libpq` |
| Generated `config.h` | detected by g++ 14 / libstdc++ 14 (e.g. `PQXX_HAVE_RANGES`, `PQXX_HAVE_YEAR_MONTH_DAY`, no `PQXX_HAVE_STACKTRACE`) | manifest `config_h` |
| Consumer compiler | GCC ≥ 14 (the feature macros above were detected with it) | profile `consumers` |
| Shared libraries a program gains | `libpq.so.5` plus the C/C++ runtime it already has; nothing else (whole-archive link probe) | manifest `requirements.needed_shared_libraries` |

The headers contain no declarations that depend on the consumer's language level (only an
`unreachable` helper macro), so a C++23 consumer of the C++20-built archive is ODR-safe; this is
also exercised by the tests.

### Tested (tests/containers/run, tests/sdk-test, tests/deb-test)

| Environment | Compiler | libpq5 | Result |
|---|---|---|---|
| ubuntu:24.04, clean (no libpq-dev, no libpqxx) | g++-14 14.2.0 | 16.15 | pass |
| ubuntu:24.04 + Ubuntu `libpqxx-dev` 7.8.1 | g++-14 14.2.0 | 16.15 | pass, system libpqxx unused |
| development host, 24.04 + legacy `libpqxx-dev` 7.10.4 (old fork) | g++-14 14.2.0 | 16.15 | pass, system libpqxx unused |
| ubuntu:26.04 LTS | g++ 15.2.0 (default) | 18.6 | pass |
| ubuntu:26.04 LTS | g++-14 14.3.0 | 18.6 | pass |
| ubuntu:26.10 (still a development branch when tested) | g++ 15.3.0 | 18.6 | pass (informational) |
| ubuntu:24.04, `libpqxx-vh-dev` installed next to Ubuntu `libpqxx-dev` | g++-14 | 16.15 | pass |

Each run builds C++23, C++20 and C++23-unity consumers with `-Wall -Wextra -Wpedantic -Werror -O3`
and runs them against a throwaway PostgreSQL (16 on 24.04, 18 on 26.x). So **one artifact serves
Ubuntu 24.04 and 26.04 LTS**; no per-release artifact is needed today.

Not supported by this profile: Debian, other distributions, clang/libc++ consumers, GCC < 14,
`_GLIBCXX_USE_CXX11_ABI=0`, Ubuntu < 24.04, non-amd64.

## Matrix policy

- One profile per (OS baseline, architecture). A newer Ubuntu release is supported by the existing
  profile once it is listed in `consumers.tested_images` and passes; it gets its own profile only
  if a test proves the baseline artifact incompatible.
- Next: `ubuntu-24.04-arm64` (`-march=armv8-a`, `-mbranch-protection=standard`), built natively on
  an arm64 runner (this host has no arm64 emulation). Add it as a second `[[debian.packages]]`
  contract and `tests/containers` cases once amd64 releases have been reliable. No
  microarchitecture-specific variants without a measured need.
- A change of compiler major, C++ ABI or libpq SONAME is a new profile (or a new SDK revision of
  the same profile if the old one is withdrawn), never a silent rebuild under the same identity.

# Patches

None. libpqxx is built from the unmodified official release archive with upstream's CMake build
(`tools/pqxx-sdk build`), configured entirely through standard CMake options.

A patch is added only when upstream behaviour prevents a correct SDK and no configuration option
avoids it. Each `NNNN-description.patch` (applied with `patch -p1` in name order after the source
archive is verified) must be listed here with its purpose and the upstream issue/PR that makes it
unnecessary; `pqxx-sdk check` enforces the listing, and the SDK ships every applied patch under
`licenses/patches/` and records its sha256 in `manifest.json`.

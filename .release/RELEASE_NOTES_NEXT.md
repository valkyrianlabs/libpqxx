<!--
vl-release staged release notes: the user-facing story of the NEXT release.
Maintained continuously while working; `vlr prepare` promotes it into the release notes
history (and the GitHub release) and resets this file to this template.

Format: the first line is "# <release title>" WITHOUT a version number (vlr adds it).
Everything after the title is the Markdown release body. Describe the resulting behavior
for users and operators; keep it representative of what actually ships.
-->
# Published to the Valkyrian Labs APT repository

`libpqxx-vh-dev` now comes from `apt.valkyrianlabs.com`, the repository that also serves Vaulthalla, PDFium and the
other Valkyrian Labs tools, signed by the Valkyrian Labs LLC key (`494D 71A1 EEDD F87B D48E  5048 6D64 CB7B DFC3 0EC6`).
`apt.vaulthalla.sh` serves the same index, so existing sources keep working. The SDK is the same libpqxx 8.0.2,
rebuilt on the current Ubuntu 24.04 toolchain.

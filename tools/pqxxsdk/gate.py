"""Auto-release gate for an upstream candidate (`tools/upstream-candidate`, `upstream.yml`).

`upstream-candidate` writes release/build/upstream-candidate/result.json. A candidate is released
without a maintainer only when nothing in it needs judgement:

  status          the SDK build with upstream's tests, `inspect`, the consumer and acquisition
                  tests passed
  patch release   same upstream major.minor (a minor or major release can change the API and
                  is always a maintainer's release)
  build files     upstream's build-system files and requirements.json are unchanged since the
                  pinned release (new options, flags or minimum versions may need profile changes)

Anything else stays an ordinary pull request, labelled for review, with the reasons listed.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

_UPSTREAM = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def is_patch_release(old: str, new: str) -> bool:
    """`new` is a later x.y.Z of `old`'s x.y (both stable upstream versions)."""
    a, b = _UPSTREAM.match(old or ""), _UPSTREAM.match(new or "")
    if not a or not b:
        return False
    return a.group(1, 2) == b.group(1, 2) and int(b.group(3)) > int(a.group(3))


def blockers(result: dict) -> list[str]:
    """Why `result` must not be released automatically; empty means it may be."""
    found = []
    if result.get("status") != "passed":
        found.append("the candidate failed: see the build and test log in the report")
    old, new = result.get("old_version", ""), result.get("new_version", "")
    if not is_patch_release(old, new):
        found.append(f"libpqxx {old} -> {new} is not a patch release of the same major.minor")
    if result.get("requirements_changed", True):
        found.append("upstream requirements.json changed")
    if result.get("build_files_changed", True):
        found.append("upstream build-system files changed")
    if not result.get("release"):
        found.append("no candidate VERSION recorded")
    return found


def run_gate(path: str, *, github_output: str | None) -> int:
    """Print the decision; with `github_output` ("" = $GITHUB_OUTPUT) also append release/version/blockers."""
    result = json.loads(Path(path).read_text())
    found = blockers(result)
    release = not found
    print(f"gate: libpqxx {result.get('new_version')} (VERSION {result.get('release')}): "
          + ("release automatically" if release else "needs a maintainer"))
    for reason in found:
        print(f"  - {reason}")
    output = os.environ.get("GITHUB_OUTPUT") if github_output == "" else github_output
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"release={'true' if release else 'false'}\n")
            handle.write(f"version={result.get('release') or ''}\n")
            handle.write("blockers<<GATE_EOF\n" + "".join(f"- {reason}\n" for reason in found) + "GATE_EOF\n")
    return 0 if release else 1

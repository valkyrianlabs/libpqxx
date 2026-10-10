"""Paths, configuration files and small helpers shared by every command."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCK_PATH = ROOT / "upstream.lock.json"
PROFILES_DIR = ROOT / "profiles"
PATCHES_DIR = ROOT / "patches"
VERSION_PATH = ROOT / "VERSION"

# Everything generated lives below release/ (ignored). vl-release uses release/build as its build
# directory (deb/ is its package build tree) and release/dist as its output directory.
WORK = ROOT / "release" / "build"
DOWNLOADS = WORK / "downloads"
SOURCE = WORK / "source"
COMPILE = WORK / "compile"
STAGE = WORK / "stage"
SDK = WORK / "sdk"
DIST = SDK / "dist"  # the SDK release assets: archive, .sha256, .manifest.json per profile
METADATA = WORK / "metadata"

SDK_NAME = "libpqxx-sdk"
PC_NAME = "libpqxx-vh"
NEXUS_ARCHIVE_NAME = "libpqxx-sdk.tar.zst"
MANIFEST_SCHEMA = 1

SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")


class SdkError(Exception):
    """A failure with a message meant for the operator (no traceback)."""


def log(message: str) -> None:
    print(f"pqxx-sdk: {message}", file=sys.stderr, flush=True)


def run(cmd: list[str], *, cwd: Path | None = None, env: dict | None = None, capture: bool = False,
        check: bool = True) -> subprocess.CompletedProcess:
    log("$ " + shlex.join(str(c) for c in cmd))
    result = subprocess.run([str(c) for c in cmd], cwd=cwd, env=env, text=True,
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None)
    if check and result.returncode != 0:
        detail = f"\n{result.stderr.strip()}" if capture and result.stderr else ""
        raise SdkError(f"command failed ({result.returncode}): {shlex.join(str(c) for c in cmd)}{detail}")
    return result


def output(cmd: list[str], **kwargs) -> str:
    return subprocess.run([str(c) for c in cmd], text=True, check=True, stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, **kwargs).stdout.strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SdkError(f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path} is missing") from None


# --- versions ------------------------------------------------------------------------------------
#
# VERSION is the SDK version itself: UPSTREAM-REVISION, vl-release's `debian-upstream` version
# policy. 8.0.2-1 is upstream libpqxx 8.0.2, SDK (packaging) revision 1; the same string is the Git
# tag (v8.0.2-1), the Debian package version and the Nexus path segment.
#
#   packaging-only change:  vlr version bump revision / vlr cut revision     8.0.2-1 -> 8.0.2-2
#   new upstream release:   tools/pqxx-sdk pin; vlr version upstream 8.0.3   -> 8.0.3-1

_SDK_VERSION = re.compile(r"(?P<upstream>(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*))-(?P<revision>[1-9]\d*)")


@dataclass(frozen=True)
class SdkVersion:
    upstream: str
    revision: int

    @property
    def sdk(self) -> str:
        return f"{self.upstream}-{self.revision}"


def parse_semver(raw: str) -> tuple[int, int, int]:
    match = SEMVER.fullmatch(raw.strip())
    if not match:
        raise SdkError(f"invalid version {raw!r}: expected MAJOR.MINOR.PATCH")
    return tuple(int(p) for p in match.groups())  # type: ignore[return-value]


def parse_sdk_version(raw: str) -> SdkVersion:
    match = _SDK_VERSION.fullmatch(raw.strip())
    if not match:
        raise SdkError(f"invalid SDK version {raw.strip()!r}: expected UPSTREAM-REVISION, e.g. 8.0.2-1")
    return SdkVersion(match.group("upstream"), int(match.group("revision")))


def release_version() -> SdkVersion:
    return parse_sdk_version(VERSION_PATH.read_text(encoding="utf-8"))


# --- configuration -------------------------------------------------------------------------------


def load_lock() -> dict:
    lock = read_json(LOCK_PATH)
    if lock.get("schema") != 1:
        raise SdkError("upstream.lock.json: unsupported schema")
    return lock


def profile_ids() -> list[str]:
    return sorted(p.stem for p in PROFILES_DIR.glob("*.toml"))


def load_profile(profile_id: str) -> dict:
    path = PROFILES_DIR / f"{profile_id}.toml"
    if not path.is_file():
        raise SdkError(f"unknown profile {profile_id!r}; available: {', '.join(profile_ids())}")
    with path.open("rb") as handle:
        profile = tomllib.load(handle)
    if profile.get("id") != profile_id:
        raise SdkError(f"{path.name}: id must be {profile_id!r}")
    return profile


def patch_files() -> list[Path]:
    return sorted(PATCHES_DIR.glob("*.patch"))


def dist_names(version: SdkVersion, profile_id: str) -> dict[str, str]:
    base = f"{SDK_NAME}-{version.sdk}-{profile_id}"
    return {
        "top": base,
        "archive": f"{base}.tar.zst",
        "sha256": f"{base}.tar.zst.sha256",
        "manifest": f"{base}.manifest.json",
    }


def nexus_path(version: SdkVersion, profile_id: str) -> str:
    """Directory of one SDK artifact inside the raw repository (immutable once published)."""
    return f"libpqxx/{version.sdk}/{profile_id}"


def git_head() -> str | None:
    try:
        return output(["git", "-C", ROOT, "rev-parse", "HEAD"])
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def source_date_epoch(lock: dict) -> int:
    """Timestamp for every archive member: SOURCE_DATE_EPOCH, else the repository commit, else
    the upstream commit (so rebuilding the same inputs yields the same archive bytes)."""
    if os.environ.get("SOURCE_DATE_EPOCH", "").isdigit():
        return int(os.environ["SOURCE_DATE_EPOCH"])
    try:
        return int(output(["git", "-C", ROOT, "log", "-1", "--format=%ct"]))
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return int(lock["upstream"]["commit_epoch"])

"""Verify an SDK archive without trusting the build tree: layout, safe members, every file's
sha256 against the embedded manifest, header include closure, pkg-config metadata, and (given the
pinned source tree) that the shipped headers are upstream's, byte for byte."""

from __future__ import annotations

import io
import json
import re
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

from .common import PC_NAME, SDK_NAME, SdkError, sha256_file

REQUIRED = [
    "manifest.json",
    "include/pqxx/pqxx",
    "include/pqxx/connection.hxx",
    "lib/libpqxx.a",
    f"lib/pkgconfig/{PC_NAME}.pc",
    "licenses/libpqxx/COPYING",
    "licenses/NOTICE.md",
]
FORBIDDEN = [r"\.so(\.|$)", r"^lib/cmake/", r"^lib/pkgconfig/libpqxx\.pc$", r"^share/", r"^bin/", r"\.la$"]
INCLUDE = re.compile(r'^\s*#\s*include\s*[<"](pqxx/[^>"]+)[>"]', re.MULTILINE)


def read_archive(path: Path) -> dict[str, bytes]:
    """Members of a .tar.zst as {relative path: bytes}, after rejecting anything unsafe."""
    try:
        raw = subprocess.run(["zstd", "-dc", str(path)], check=True, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE).stdout
    except subprocess.CalledProcessError as exc:
        raise SdkError(f"{path.name}: not a valid zstd stream ({exc.stderr.decode().strip()})") from None
    files: dict[str, bytes] = {}
    tops = set()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as tar:
        for member in tar.getmembers():
            name = PurePosixPath(member.name)
            if name.is_absolute() or ".." in name.parts:
                raise SdkError(f"{path.name}: unsafe member path {member.name}")
            if not (member.isfile() or member.isdir()):
                raise SdkError(f"{path.name}: member {member.name} is not a regular file or directory")
            if member.uid != 0 or member.gid != 0 or member.mode & 0o7022:
                raise SdkError(f"{path.name}: member {member.name} has ownership/mode {member.uid}:{member.gid} "
                               f"{oct(member.mode)}")
            tops.add(name.parts[0])
            if member.isfile():
                rel = PurePosixPath(*name.parts[1:]).as_posix()
                files[rel] = tar.extractfile(member).read()  # type: ignore[union-attr]
    if len(tops) != 1:
        raise SdkError(f"{path.name}: expected a single top-level directory, found {sorted(tops)}")
    files["\0top"] = next(iter(tops)).encode()
    return files


def inspect(archive: Path, *, sidecar: Path | None = None, source: Path | None = None,
            expect_profile: str | None = None, expect_sdk_version: str | None = None) -> dict:
    files = read_archive(archive)
    top = files.pop("\0top").decode()
    problems: list[str] = []

    for rel in REQUIRED:
        if rel not in files:
            problems.append(f"missing {rel}")
    for rel in files:
        for pattern in FORBIDDEN:
            if re.search(pattern, rel):
                problems.append(f"forbidden member {rel}")
    if problems:
        raise SdkError(f"{archive.name}:\n  - " + "\n  - ".join(problems))

    manifest = json.loads(files["manifest.json"])
    if manifest.get("name") != SDK_NAME or manifest.get("schema") != 1:
        problems.append("manifest.json is not a libpqxx-sdk schema 1 manifest")
    expected_top = f"{SDK_NAME}-{manifest.get('sdk_version')}-{manifest.get('profile')}"
    if top != expected_top:
        problems.append(f"top directory {top} != {expected_top}")
    if expect_profile and manifest.get("profile") != expect_profile:
        problems.append(f"profile {manifest.get('profile')} != expected {expect_profile}")
    if expect_sdk_version and manifest.get("sdk_version") != expect_sdk_version:
        problems.append(f"SDK version {manifest.get('sdk_version')} != expected {expect_sdk_version}")

    generated = manifest.get("generated_headers") or []
    if not generated:
        problems.append("manifest.json lists no generated configuration header")
    for rel in generated:
        if f"include/{rel}" not in files:
            problems.append(f"generated header include/{rel} is missing")

    # Every file is listed with its digest, and nothing unlisted ships.
    import hashlib
    listed = manifest.get("files", {})
    actual = {rel: hashlib.sha256(data).hexdigest() for rel, data in files.items() if rel != "manifest.json"}
    for rel in sorted(set(listed) | set(actual)):
        if listed.get(rel) != actual.get(rel):
            problems.append(f"{rel}: manifest {listed.get(rel, '(absent)')[:12]} vs archive {actual.get(rel, '(absent)')[:12]}")

    # Headers: every pqxx include reachable from <pqxx/pqxx> (and from any shipped header) resolves.
    headers = {rel[len("include/"):] for rel in files if rel.startswith("include/")}
    for rel in sorted(headers):
        for inc in INCLUDE.findall(files["include/" + rel].decode("utf-8", "replace")):
            if inc not in headers:
                problems.append(f"{rel} includes {inc}, which the SDK does not ship")
    if source is not None:
        for rel in sorted(headers):
            if rel in generated:
                continue  # generated by the build; checked against the manifest instead
            upstream_file = source / "include" / rel
            if not upstream_file.is_file():
                problems.append(f"{rel} is not an upstream header")
            elif sha256_file(upstream_file) != actual["include/" + rel]:
                problems.append(f"{rel} differs from upstream")

    # The generated headers must say what the manifest says the build detected.
    config = "\n".join(files[f"include/{rel}"].decode() for rel in generated if f"include/{rel}" in files)
    for macro, enabled in manifest.get("config_h", {}).items():
        defined = re.search(rf"^#define {macro}\b", config, re.MULTILINE) is not None
        if defined != enabled:
            problems.append(f"config.h {macro} defined={defined}, manifest says {enabled}")

    pc = files[f"lib/pkgconfig/{PC_NAME}.pc"].decode()
    if "prefix=${pcfiledir}/../.." not in pc:
        problems.append(".pc prefix is not relative to ${pcfiledir}")
    if re.search(r"(^|[=\s])/(?!/)", pc.split("\n", 2)[-1].replace("${", "")):
        problems.append(".pc contains an absolute path")
    if "Libs: ${libdir}/libpqxx.a" not in pc or re.search(r"(^|\s)-lpqxx\b", pc):
        problems.append(".pc does not link the packaged archive by path")
    if re.search(r"^Requires(\.private)?:\s*\S", pc, re.MULTILINE) or re.search(r"(^|\s)-lpq(\s|$)", pc):
        problems.append(".pc must bind libpq by SONAME (-l:libpq.so.5), not Requires/-lpq")
    upstream_version = manifest.get("upstream", {}).get("version")
    if f"\nVersion: {upstream_version}\n" not in pc:
        problems.append(f".pc Version is not {upstream_version}")

    if sidecar is not None:
        side = json.loads(sidecar.read_text())
        info = side.pop("archive", {})
        if info.get("sha256") != sha256_file(archive) or info.get("size") != archive.stat().st_size:
            problems.append(f"{sidecar.name}: archive sha256/size do not match {archive.name}")
        if side != manifest:
            problems.append(f"{sidecar.name}: differs from the manifest inside the archive")

    if problems:
        raise SdkError(f"{archive.name}:\n  - " + "\n  - ".join(problems))
    return {
        "archive": archive.name,
        "sha256": sha256_file(archive),
        "sdk_version": manifest["sdk_version"],
        "profile": manifest["profile"],
        "upstream_commit": manifest["upstream"]["commit"],
        "files": len(files),
        "headers": len(headers),
        "libpqxx_a_bytes": len(files["lib/libpqxx.a"]),
        "headers_checked_against_source": source is not None,
    }

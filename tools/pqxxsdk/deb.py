"""Install a verified SDK archive into a Debian package tree (debian/rules, optional package).

    <destdir>/usr/lib/<multiarch>/libpqxx-vh/        the SDK tree, byte for byte (manifest.json)
    <destdir>/usr/lib/<multiarch>/pkgconfig/libpqxx-vh.pc   same metadata with a fixed prefix
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path

from .build import render_pc
from .common import PC_NAME, SdkError, dist_names, load_profile, log, release_version, sha256_file
from .inspect import inspect


def deb_install(profile_id: str, dist: Path, destdir: Path, multiarch: str, substvars: Path | None) -> None:
    profile = load_profile(profile_id)
    if profile["target"]["gnu_triplet"] != multiarch:
        raise SdkError(f"profile {profile_id} is {profile['target']['gnu_triplet']}, the package is {multiarch}")
    version = release_version()
    names = dist_names(version, profile_id)
    archive = dist / names["archive"]
    if not archive.is_file():
        raise SdkError(f"{archive} is missing: run `tools/pqxx-sdk build --profile {profile_id}` before "
                       "`vlr build-deb` (the package repackages that archive)")
    inspect(archive, sidecar=dist / names["manifest"], expect_profile=profile_id, expect_sdk_version=version.sdk)

    prefix = f"/usr/lib/{multiarch}/libpqxx-vh"
    target = destdir / prefix.lstrip("/")
    if target.exists():
        shutil.rmtree(target)
    with tempfile.TemporaryDirectory() as tmp:
        import subprocess
        import tarfile
        import io
        raw = subprocess.run(["zstd", "-dc", str(archive)], check=True, stdout=subprocess.PIPE).stdout
        with tarfile.open(fileobj=io.BytesIO(raw)) as tar:
            tar.extractall(tmp, filter="data")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(Path(tmp) / names["top"], target)

    manifest = json.loads((target / "manifest.json").read_text())
    for rel, digest in manifest["files"].items():
        if sha256_file(target / rel) != digest:
            raise SdkError(f"{rel} does not match manifest.json after installation")

    pc_dir = destdir / "usr" / "lib" / multiarch / "pkgconfig"
    pc_dir.mkdir(parents=True, exist_ok=True)
    (pc_dir / f"{PC_NAME}.pc").write_text(render_pc(version, profile, prefix=prefix))
    for path in [target, *target.rglob("*"), pc_dir / f"{PC_NAME}.pc"]:
        path.chmod(0o755 if path.is_dir() else 0o644)

    if substvars:
        libpq_min = manifest["requirements"]["libpq"]["min_version"] or "13"
        libpq_min = re.sub(r"\.0$", "", libpq_min)
        with substvars.open("a", encoding="utf-8") as out:
            out.write(f"sdk:Version={version.sdk}\nsdk:Upstream={version.upstream}\nsdk:Profile={profile_id}\n"
                      f"sdk:LibpqMin={libpq_min}\n")
    log(f"installed SDK {version.sdk} ({profile_id}) at {prefix} with {len(manifest['files'])} verified files")

"""Publish SDK archives to a Nexus Raw hosted repository, and verify what is published.

Same rules as `vlr publish-deb` (which handles the optional .deb):

- per remote file: absent -> upload; identical sha256 -> skip; different -> refuse (exit 3);
  unreadable -> refuse. Published bytes are never replaced; a packaging change is a new SDK
  revision and therefore a new path.
- nothing is uploaded unless every archive passed inspection and the consumer test record for
  exactly those bytes says it passed.
- credentials reach curl through `--config -` on stdin, never argv.
- RELEASE_PUBLISH_MODE gates uploads (`nexus`/`enabled` upload; default `disabled`).

Remote layout, per SDK version and profile (all three files are immutable):

    <NEXUS_RAW_REPO>/libpqxx/<upstream>-<revision>/<profile>/libpqxx-sdk.tar.zst
                                                             libpqxx-sdk.tar.zst.sha256
                                                             manifest.json
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from base64 import b64encode
from pathlib import Path

from .common import (NEXUS_ARCHIVE_NAME, ROOT, SdkError, dist_names, load_lock, log, nexus_path,
                     release_version, sha256_file)
from .inspect import inspect

DEFAULT_OUTPUT = ROOT / "release" / "dist"
EXIT_REFUSED = 3


class Refused(SdkError):
    pass


def _credentials() -> tuple[str, str] | None:
    user = os.environ.get("NEXUS_USER")
    password = os.environ.get("NEXUS_PASSWORD") or os.environ.get("NEXUS_PASS")
    return (user, password) if user and password else None


def base_url() -> str:
    url = os.environ.get("NEXUS_RAW_REPO", "").rstrip("/")
    if not url.startswith("https://") and not url.startswith("http://127.0.0.1"):
        raise SdkError("NEXUS_RAW_REPO must be the raw repository URL, e.g. "
                       "https://repo.valkyrianlabs.com/repository/cpp-sdk")
    if url.startswith("http://") and os.environ.get("PQXX_SDK_ALLOW_LOCAL_NEXUS") != "1":
        raise SdkError("plain-http Nexus URLs are only for tests (PQXX_SDK_ALLOW_LOCAL_NEXUS=1)")
    return url


def fetch(url: str, *, auth: bool = True) -> bytes | None:
    """Remote bytes, or None when absent (404). Anything else is an error: never guess."""
    request = urllib.request.Request(url, headers={"User-Agent": "vaulthalla-libpqxx-sdk"})
    creds = _credentials() if auth else None
    if creds:
        request.add_header("Authorization", "Basic " + b64encode(f"{creds[0]}:{creds[1]}".encode()).decode())
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise Refused(f"GET {url}: HTTP {exc.code}; cannot tell what is published, refusing") from None
    except urllib.error.URLError as exc:
        raise Refused(f"GET {url}: {exc.reason}; cannot tell what is published, refusing") from None


def upload(path: Path, url: str) -> None:
    creds = _credentials()
    if not creds:
        raise SdkError("NEXUS_USER and NEXUS_PASSWORD are required to upload")
    config = f'user = "{creds[0]}:{creds[1]}"\n'
    log(f"PUT {url}")
    result = subprocess.run(["curl", "--fail-with-body", "--silent", "--show-error", "--retry", "3",
                             "--retry-all-errors", "--max-time", "900", "--config", "-",
                             "--upload-file", str(path), url], input=config, text=True, capture_output=True)
    if result.returncode != 0:
        raise SdkError(f"upload {url} failed: {result.stderr.strip()} {result.stdout.strip()[:500]}")


def planned_files(output_dir: Path) -> list[dict]:
    """The remote files for every SDK archive in the output directory, after the publication gate."""
    version = release_version()
    lock = load_lock()
    entries = []
    sidecars = sorted(output_dir.glob(f"libpqxx-sdk-{version.sdk}-*.manifest.json"))
    if not sidecars:
        raise SdkError(f"no libpqxx-sdk {version.sdk} archives in {output_dir}")
    for sidecar in sidecars:
        manifest = json.loads(sidecar.read_text())
        profile = manifest["profile"]
        names = dist_names(version, profile)
        archive = output_dir / names["archive"]
        report = inspect(archive, sidecar=sidecar, expect_profile=profile, expect_sdk_version=version.sdk)
        if manifest["upstream"] != lock["upstream"]:
            raise SdkError(f"{sidecar.name}: upstream provenance differs from upstream.lock.json")
        tests = output_dir / f"libpqxx-sdk-{version.sdk}-{profile}.tests.json"
        record = json.loads(tests.read_text()) if tests.is_file() else {}
        if record.get("result") != "passed" or record.get("archive_sha256") != report["sha256"]:
            raise SdkError(f"{archive.name}: no passing consumer test record for these bytes ({tests.name}); "
                           "run tests/sdk-test before publishing")
        remote_dir = nexus_path(version, profile)
        sha_line = f"{report['sha256']}  {NEXUS_ARCHIVE_NAME}\n".encode()
        tmp = Path(tempfile.mkdtemp(prefix="pqxx-sdk-publish-"))
        (tmp / "sha256").write_bytes(sha_line)
        # Order matters: the archive first, manifest.json last, so a complete manifest means a
        # complete directory.
        entries += [
            {"local": archive, "remote": f"{remote_dir}/{NEXUS_ARCHIVE_NAME}"},
            {"local": tmp / "sha256", "remote": f"{remote_dir}/{NEXUS_ARCHIVE_NAME}.sha256"},
            {"local": sidecar, "remote": f"{remote_dir}/manifest.json"},
        ]
    return entries


def preflight(base: str) -> dict:
    """The target must be an existing Nexus *raw hosted* repository. A missing repository answers
    404 like a missing file would, so without this check a typo would look like "absent: upload"."""
    import re
    match = re.match(r"^(https?://[^/]+(?:/.*?)?)/repository/([^/]+)$", base)
    if not match:
        raise Refused(f"{base} is not a Nexus repository URL (<nexus>/repository/<name>)")
    root, name = match.groups()
    listing = fetch(f"{root}/service/rest/v1/repositories")
    if listing is None:
        raise Refused(f"{root}: repository API unavailable; cannot verify {name}")
    repos = {r.get("name"): r for r in json.loads(listing)}
    repo = repos.get(name)
    if repo is None:
        raise Refused(f"Nexus repository {name!r} does not exist at {root} (create a raw hosted repository, "
                      "docs/RELEASING.md); existing raw repositories: "
                      + (", ".join(n for n, r in repos.items() if r.get("format") == "raw") or "none"))
    if repo.get("format") != "raw" or repo.get("type") != "hosted":
        raise Refused(f"Nexus repository {name!r} is {repo.get('format')}/{repo.get('type')}, not raw/hosted")
    log(f"target: Nexus raw hosted repository {name} ({repo.get('url', base)})")
    return repo


def plan(entries: list[dict], base: str) -> list[dict]:
    for entry in entries:
        entry["url"] = f"{base}/{entry['remote']}"
        entry["sha256"] = sha256_file(entry["local"])
        remote = fetch(entry["url"])
        if remote is None:
            entry["action"] = "upload"
        elif hashlib.sha256(remote).hexdigest() == entry["sha256"]:
            entry["action"] = "skip"
        else:
            entry["action"] = "refuse"
    refused = [e["remote"] for e in entries if e["action"] == "refuse"]
    for entry in entries:
        log(f"{entry['action']:>7}  {entry['remote']}")
    if refused:
        raise Refused("already published with different bytes (publish a new SDK revision instead): "
                      + ", ".join(refused))
    return entries


def publish(*, output_dir: Path = DEFAULT_OUTPUT, dry_run: bool = False, require_enabled: bool = False,
            mode: str | None = None) -> list[dict]:
    mode = mode or os.environ.get("RELEASE_PUBLISH_MODE", "disabled")
    enabled = mode in ("nexus", "enabled")
    entries = planned_files(output_dir)
    if not enabled and not dry_run:
        if require_enabled:
            raise SdkError(f"RELEASE_PUBLISH_MODE={mode}: publication is disabled")
        log(f"RELEASE_PUBLISH_MODE={mode}: not publishing (gate passed for {len(entries)} files)")
        return entries
    base = base_url()
    preflight(base)
    plan(entries, base)
    if dry_run:
        log("dry run: nothing uploaded")
        return entries
    for entry in entries:
        if entry["action"] == "upload":
            upload(entry["local"], entry["url"])
    verify(entries)
    return entries


def verify(entries: list[dict], *, timeout: int = 300) -> None:
    """Download every published file and compare sha256 (anonymously first, as consumers do)."""
    deadline = time.monotonic() + timeout
    pending = list(entries)
    while pending:
        still = []
        for entry in pending:
            try:
                data = fetch(entry["url"], auth=False)
            except Refused:
                data = fetch(entry["url"])
            if data is not None and hashlib.sha256(data).hexdigest() == entry["sha256"]:
                log(f"verified {entry['remote']}")
            else:
                still.append(entry)
        pending = still
        if pending:
            if time.monotonic() > deadline:
                raise SdkError("not available with the expected sha256: " + ", ".join(e["remote"] for e in pending))
            time.sleep(10)


def verify_published(output_dir: Path = DEFAULT_OUTPUT, timeout: int = 300) -> None:
    entries = planned_files(output_dir)
    base = base_url()
    preflight(base)
    for entry in entries:
        entry["url"] = f"{base}/{entry['remote']}"
        entry["sha256"] = sha256_file(entry["local"])
    verify(entries, timeout=timeout)

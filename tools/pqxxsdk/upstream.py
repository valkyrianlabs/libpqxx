"""Official upstream releases: discover (GitHub releases), pin (upstream.lock.json) and fetch the
pinned source archive with its provenance verified.

Only the pinned archive is ever built. Discovery is read-only; pinning is an explicit operation
whose result is reviewed and committed like any other change."""

from __future__ import annotations

import json
import os
import re
import shutil
import tarfile
import tempfile
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from .common import (DOWNLOADS, LOCK_PATH, SOURCE, SdkError, SdkVersion, load_lock, log, patch_files,
                     release_version, run, sha256_file, write_json)

REPOSITORY = "jtv/libpqxx"
API = "https://api.github.com"
STABLE_TAG = re.compile(r"(\d+)\.(\d+)\.(\d+)")  # upstream tags stable releases as bare X.Y.Z


def _request(url: str, *, accept: str = "application/vnd.github+json") -> urllib.request.Request:
    headers = {"Accept": accept, "User-Agent": "vaulthalla-libpqxx-sdk"}
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and url.startswith(API):
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def _get_json(url: str) -> tuple[object, str | None]:
    try:
        with urllib.request.urlopen(_request(url), timeout=60) as response:
            link = response.headers.get("Link")
            return json.load(response), link
    except urllib.error.HTTPError as exc:
        raise SdkError(f"GitHub API {url}: HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise SdkError(f"GitHub API {url}: {exc.reason}") from None


def _next_link(link: str | None) -> str | None:
    for part in (link or "").split(","):
        match = re.search(r'<([^>]+)>;\s*rel="next"', part)
        if match:
            return match.group(1)
    return None


def stable_releases(include_prereleases: bool = False) -> list[dict]:
    """Published GitHub releases, newest *version* first (not newest date: upstream publishes
    maintenance releases of older series after newer majors, e.g. 7.10.7 after 8.0.2)."""
    url: str | None = f"{API}/repos/{REPOSITORY}/releases?per_page=100"
    releases = []
    while url:
        page, link = _get_json(url)
        for item in page:  # type: ignore[union-attr]
            if item.get("draft"):
                continue
            tag = item["tag_name"]
            match = STABLE_TAG.fullmatch(tag)
            if item.get("prerelease") or not match:
                if not include_prereleases:
                    continue
            key = tuple(int(g) for g in match.groups()) if match else (-1, -1, -1)
            releases.append({"tag": tag, "version": tag, "name": item.get("name") or tag,
                             "prerelease": bool(item.get("prerelease")) or not match,
                             "published_at": item.get("published_at"), "url": item.get("html_url"),
                             "_key": key})
        url = _next_link(link)
    releases.sort(key=lambda r: r["_key"], reverse=True)
    for release in releases:
        del release["_key"]
    return releases


def discover() -> dict:
    lock = load_lock()
    pinned = lock["upstream"]["version"]
    releases = stable_releases()
    if not releases:
        raise SdkError("no stable upstream releases found")
    latest = releases[0]
    key = lambda v: tuple(int(p) for p in v.split("."))  # noqa: E731
    newer = [r for r in releases if key(r["version"]) > key(pinned)]
    return {
        "repository": REPOSITORY,
        "pinned": pinned,
        "latest": latest["version"],
        "latest_published_at": latest["published_at"],
        "update_available": key(latest["version"]) > key(pinned),
        "newer": [r["version"] for r in newer],
        "stable": [r["version"] for r in releases[:10]],
    }


def _resolve_tag(tag: str) -> dict:
    ref, _ = _get_json(f"{API}/repos/{REPOSITORY}/git/ref/tags/{tag}")
    obj = ref["object"]  # type: ignore[index]
    if obj["type"] == "tag":  # annotated tag: dereference to the commit
        tag_obj, _ = _get_json(f"{API}/repos/{REPOSITORY}/git/tags/{obj['sha']}")
        obj = tag_obj["object"]  # type: ignore[index]
    if obj["type"] != "commit":
        raise SdkError(f"tag {tag} does not point at a commit")
    commit, _ = _get_json(f"{API}/repos/{REPOSITORY}/git/commits/{obj['sha']}")
    date = commit["committer"]["date"]  # type: ignore[index]
    return {"commit": commit["sha"], "tree": commit["tree"]["sha"], "commit_date": date,  # type: ignore[index]
            "commit_epoch": int(datetime.fromisoformat(date.replace("Z", "+00:00")).timestamp())}


def archive_url(tag: str) -> str:
    return f"https://github.com/{REPOSITORY}/archive/refs/tags/{tag}.tar.gz"


def _download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    try:
        with urllib.request.urlopen(_request(url, accept="*/*"), timeout=300) as response, tmp.open("wb") as out:
            shutil.copyfileobj(response, out)
    except urllib.error.HTTPError as exc:
        tmp.unlink(missing_ok=True)
        raise SdkError(f"download {url}: HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        tmp.unlink(missing_ok=True)
        raise SdkError(f"download {url}: {exc.reason}") from None
    tmp.replace(target)


def archive_facts(path: Path) -> dict:
    """The commit id git-archive embeds in the pax global header, and the single top directory."""
    with tarfile.open(path, "r:gz") as tar:
        commit = tar.pax_headers.get("comment", "").strip()
        tops = {member.name.split("/", 1)[0] for member in tar.getmembers()}
        version_member = None
        if len(tops) == 1:
            try:
                version_member = tar.extractfile(f"{next(iter(tops))}/VERSION")
            except KeyError:
                version_member = None
        version = version_member.read().decode().strip() if version_member else None
    return {"commit": commit, "top_dir": next(iter(tops)) if len(tops) == 1 else None, "version": version}


def pin(version: str | None) -> dict:
    """Resolve an upstream release (default: the newest stable one) and write upstream.lock.json."""
    releases = stable_releases(include_prereleases=bool(version))
    if version is None:
        release = next((r for r in releases if not r["prerelease"]), None)
    else:
        release = next((r for r in releases if r["tag"] == version), None)
    if release is None:
        raise SdkError(f"no published upstream release {version or '(stable)'} in {REPOSITORY}")
    if release["prerelease"]:
        log(f"warning: {release['tag']} is a prerelease")
    tag = release["tag"]
    facts = _resolve_tag(tag)
    url = archive_url(tag)
    target = DOWNLOADS / f"libpqxx-{tag}.tar.gz"
    _download(url, target)
    embedded = archive_facts(target)
    if embedded["commit"] != facts["commit"]:
        raise SdkError(f"archive {url} embeds commit {embedded['commit'] or '(none)'}, tag {tag} is {facts['commit']}")
    if embedded["version"] != release["version"]:
        raise SdkError(f"archive VERSION file says {embedded['version']}, release is {release['version']}")
    lock = {
        "schema": 1,
        "upstream": {
            "repository": f"https://github.com/{REPOSITORY}",
            "version": release["version"],
            "tag": tag,
            "release_name": release["name"],
            "release_url": release["url"],
            "published_at": release["published_at"],
            **facts,
            "archive": {
                "url": url,
                "sha256": sha256_file(target),
                "size": target.stat().st_size,
                "top_dir": embedded["top_dir"],
                "format": "tar.gz (GitHub tag archive; git-archive pax header carries the commit id)",
            },
            "license": {"spdx": "BSD-3-Clause", "file": "COPYING"},
        },
    }
    write_json(LOCK_PATH, lock)
    log(f"pinned libpqxx {release['version']} ({facts['commit']}) in {LOCK_PATH.name}")
    return lock


def fetch_source(*, offline: bool = False) -> Path:
    """Download (or reuse) the pinned archive, verify it, and extract a fresh, patched source tree.
    Returns the source directory."""
    upstream = load_lock()["upstream"]
    archive = upstream["archive"]
    target = DOWNLOADS / f"libpqxx-{upstream['tag']}.tar.gz"
    if target.is_file() and sha256_file(target) == archive["sha256"]:
        log(f"using cached {target.name}")
    else:
        if offline:
            raise SdkError(f"{target} is missing or does not match the lock, and --offline was given")
        log(f"downloading {archive['url']}")
        _download(archive["url"], target)
    verify_archive(target, upstream)

    dest = SOURCE / f"libpqxx-{upstream['version']}"
    if dest.exists():
        shutil.rmtree(dest)
    SOURCE.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=SOURCE) as tmp, tarfile.open(target, "r:gz") as tar:
        tar.extractall(tmp, filter="data")
        (Path(tmp) / archive["top_dir"]).rename(dest)
    for patch in patch_files():
        run(["patch", "-p1", "--forward", "--batch", "-i", patch], cwd=dest)
    return dest


def verify_archive(path: Path, upstream: dict) -> None:
    archive = upstream["archive"]
    actual = sha256_file(path)
    if actual != archive["sha256"]:
        raise SdkError(f"{path.name}: sha256 {actual} does not match the pinned {archive['sha256']}")
    facts = archive_facts(path)
    if facts["commit"] != upstream["commit"]:
        raise SdkError(f"{path.name}: embedded commit {facts['commit']} is not the pinned {upstream['commit']}")
    if facts["top_dir"] != archive["top_dir"] or facts["version"] != upstream["version"]:
        raise SdkError(f"{path.name}: layout/VERSION does not match the lock")
    log(f"verified {path.name}: sha256 {actual[:16]}…, commit {upstream['commit'][:12]}, VERSION {upstream['version']}")


def check_version_matches_lock(version: SdkVersion | None = None) -> None:
    version = version or release_version()
    pinned = load_lock()["upstream"]["version"]
    if version.upstream != pinned:
        raise SdkError(f"VERSION is {version.sdk} (upstream {version.upstream}) but upstream.lock.json pins "
                       f"{pinned}; run `vlr version upstream {pinned}`")

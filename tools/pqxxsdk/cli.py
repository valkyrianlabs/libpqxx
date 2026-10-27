"""pqxx-sdk command line."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from . import build as build_mod
from . import publish as publish_mod
from . import upstream
from .common import (ROOT, SOURCE, SdkError, dist_names, load_lock, load_profile, nexus_path,
                     patch_files, profile_ids, release_version)
from .inspect import inspect


def cmd_discover(args) -> int:
    result = upstream.discover()
    if args.github_output:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as out:
            out.write(f"update={'true' if result['update_available'] else 'false'}\n")
            out.write(f"latest={result['latest']}\npinned={result['pinned']}\n")
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        state = "UPDATE AVAILABLE" if result["update_available"] else "up to date"
        print(f"pinned {result['pinned']}, latest stable {result['latest']} "
              f"({result['latest_published_at']}): {state}")
        if result["newer"]:
            print("newer stable releases: " + ", ".join(result["newer"]))
    return 0


def cmd_pin(args) -> int:
    lock = upstream.pin(args.version)
    pinned = lock["upstream"]["version"]
    current = release_version()
    if current.upstream != pinned:
        print(f"next: vlr version upstream {pinned}   (SDK {pinned}-1), then build and test")
    return 0


def cmd_fetch_source(args) -> int:
    print(upstream.fetch_source(offline=args.offline))
    return 0


def cmd_build(args) -> int:
    result = build_mod.build(args.profile, tests=args.tests, njobs=args.jobs, offline=args.offline)
    print(json.dumps(result, indent=2))
    return 0


def cmd_inspect(args) -> int:
    archive = Path(args.archive)
    sidecar = Path(args.sidecar) if args.sidecar else None
    if sidecar is None and args.auto_sidecar:
        candidate = archive.with_name(archive.name.removesuffix(".tar.zst") + ".manifest.json")
        sidecar = candidate if candidate.is_file() else None
    source = Path(args.source) if args.source else None
    if args.pinned_source:
        source = SOURCE / f"libpqxx-{load_lock()['upstream']['version']}"
    report = inspect(archive, sidecar=sidecar, source=source, expect_profile=args.profile,
                     expect_sdk_version=args.sdk_version)
    print(json.dumps(report, indent=2))
    return 0


def cmd_version(args) -> int:
    version = release_version()
    data = {"sdk_version": version.sdk, "upstream": version.upstream,
            "revision": version.revision, "profiles": {}}
    for pid in profile_ids():
        profile = load_profile(pid)
        data["profiles"][pid] = {"status": profile.get("status"), "nexus_path": nexus_path(version, pid),
                                 **dist_names(version, pid)}
    print(json.dumps(data, indent=2))
    return 0


def cmd_render_pc(args) -> int:
    sys.stdout.write(build_mod.render_pc(release_version(), load_profile(args.profile), prefix=args.prefix))
    return 0


def cmd_deb_install(args) -> int:
    from .deb import deb_install
    deb_install(args.profile, Path(args.dist), Path(args.destdir), args.multiarch,
                Path(args.substvars) if args.substvars else None)
    return 0


def cmd_check(args) -> int:
    """Repository consistency: VERSION <-> lock, profiles <-> Containerfile, patches documented."""
    problems = []
    try:
        upstream.check_version_matches_lock()
    except SdkError as exc:
        problems.append(str(exc))
    lock = load_lock()["upstream"]
    for key in ("version", "tag", "commit", "tree"):
        if not lock.get(key):
            problems.append(f"upstream.lock.json lacks upstream.{key}")
    if not re.fullmatch(r"[0-9a-f]{64}", lock.get("archive", {}).get("sha256", "")):
        problems.append("upstream.lock.json lacks a sha256 for the source archive")
    containerfile = (ROOT / "ci" / "Containerfile").read_text()
    from_line = re.search(r"^FROM\s+(\S+)", containerfile, re.MULTILINE)
    for pid in profile_ids():
        profile = load_profile(pid)
        if profile.get("status") == "supported" and pid.endswith("-amd64"):
            if not from_line or from_line.group(1) != profile["build"]["image"]:
                problems.append(f"profiles/{pid}.toml build.image does not match ci/Containerfile FROM")
        if any("native" in flag for flag in profile["build"]["cxx_flags"]):
            problems.append(f"profiles/{pid}.toml uses host-specific CPU flags")
        if profile["build"]["lto"]:
            problems.append(f"profiles/{pid}.toml enables LTO (not supported initially)")
    readme = (ROOT / "patches" / "README.md").read_text() if (ROOT / "patches" / "README.md").is_file() else ""
    for patch in patch_files():
        if patch.name not in readme:
            problems.append(f"patches/{patch.name} is not documented in patches/README.md")
    if problems:
        print("\n".join(f"error: {p}" for p in problems), file=sys.stderr)
        return 1
    version = release_version()
    print(f"ok: SDK {version.sdk}, upstream {lock['version']} @ {lock['commit'][:12]}, "
          f"profiles: {', '.join(profile_ids())}, patches: {len(patch_files())}")
    return 0


def cmd_publish(args) -> int:
    entries = publish_mod.publish(output_dir=Path(args.output_dir), dry_run=args.dry_run,
                                  require_enabled=args.require_enabled, mode=args.mode)
    if args.json:
        print(json.dumps([{k: str(v) for k, v in e.items()} for e in entries], indent=2))
    return 0


def cmd_verify_published(args) -> int:
    publish_mod.verify_published(Path(args.output_dir), timeout=args.timeout)
    return 0


def cmd_gate(args) -> int:
    from .gate import run_gate

    status = run_gate(args.result, github_output=args.github_output)
    return 0 if args.github_output is not None else status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pqxx-sdk", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("discover", help="compare the pinned upstream with the newest stable GitHub release")
    p.add_argument("--json", action="store_true")
    p.add_argument("--github-output", action="store_true", help="append update/latest/pinned to $GITHUB_OUTPUT")
    p.set_defaults(func=cmd_discover)

    p = sub.add_parser("pin", help="pin an upstream release (default: newest stable) in upstream.lock.json")
    p.add_argument("version", nargs="?")
    p.set_defaults(func=cmd_pin)

    p = sub.add_parser("fetch-source", help="download/verify the pinned archive and extract it")
    p.add_argument("--offline", action="store_true", help="use only release/build/downloads")
    p.set_defaults(func=cmd_fetch_source)

    p = sub.add_parser("build", help="compile and package the SDK for one profile")
    p.add_argument("--profile", required=True, choices=profile_ids())
    p.add_argument("--tests", action="store_true", help="also build and run upstream's test suite (needs PG*)")
    p.add_argument("--jobs", type=int)
    p.add_argument("--offline", action="store_true")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("inspect", help="verify an SDK archive")
    p.add_argument("archive")
    p.add_argument("--sidecar")
    p.add_argument("--auto-sidecar", action="store_true", help="use <archive>.manifest.json next to it")
    p.add_argument("--source", help="upstream source tree to compare headers with")
    p.add_argument("--pinned-source", action="store_true", help="compare with release/build/source")
    p.add_argument("--profile")
    p.add_argument("--sdk-version")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("version", help="SDK identity derived from VERSION")
    p.set_defaults(func=cmd_version)

    p = sub.add_parser("render-pc", help="print the pkg-config file (e.g. with a fixed prefix)")
    p.add_argument("--profile", required=True)
    p.add_argument("--prefix", required=True)
    p.set_defaults(func=cmd_render_pc)

    p = sub.add_parser("deb-install", help="install a verified SDK archive into a Debian package tree")
    p.add_argument("--profile", required=True)
    p.add_argument("--dist", required=True)
    p.add_argument("--destdir", required=True)
    p.add_argument("--multiarch", required=True)
    p.add_argument("--substvars")
    p.set_defaults(func=cmd_deb_install)

    p = sub.add_parser("check", help="repository consistency")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("publish", help="publish SDK archives to Nexus Raw (gated, idempotent)")
    p.add_argument("--output-dir", default=str(publish_mod.DEFAULT_OUTPUT))
    p.add_argument("--dry-run", action="store_true", help="gate + read-only plan against Nexus")
    p.add_argument("--require-enabled", action="store_true")
    p.add_argument("--mode", choices=["disabled", "nexus", "enabled"])
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_publish)

    p = sub.add_parser("verify-published", help="download published files and compare sha256")
    p.add_argument("--output-dir", default=str(publish_mod.DEFAULT_OUTPUT))
    p.add_argument("--timeout", type=int, default=300)
    p.set_defaults(func=cmd_verify_published)

    p = sub.add_parser("gate", help="decide whether an upstream candidate may be released without a maintainer")
    p.add_argument("result", nargs="?", default="release/build/upstream-candidate/result.json",
                   help="the candidate's result.json (default: release/build/upstream-candidate/result.json)")
    p.add_argument("--github-output", nargs="?", const="", default=None,
                   help="append release/version/blockers to this file ($GITHUB_OUTPUT without a value) and exit 0")
    p.set_defaults(func=cmd_gate)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except publish_mod.Refused as exc:
        print(f"pqxx-sdk: refused: {exc}", file=sys.stderr)
        return publish_mod.EXIT_REFUSED
    except SdkError as exc:
        print(f"pqxx-sdk: error: {exc}", file=sys.stderr)
        return 1

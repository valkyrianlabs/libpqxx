"""Compile pinned libpqxx with upstream CMake + Ninja and package the relocatable static SDK.

    fetch_source -> configure -> build -> [upstream tests] -> cmake --install (stage)
    -> assemble SDK tree -> measure requirements -> manifest -> deterministic .tar.zst
"""

from __future__ import annotations

import functools
import io
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

from . import upstream as upstream_mod
from .common import (COMPILE, DIST, MANIFEST_SCHEMA, METADATA, PC_NAME, ROOT, SDK, SDK_NAME, STAGE, SdkError,
                     SdkVersion, dist_names, git_head, load_lock, load_profile, log, output, patch_files,
                     release_version, run, sha256_file, source_date_epoch, write_json)

# Shared objects a program linking the whole archive may need: libpq plus the C/C++ runtime.
ALLOWED_NEEDED = {"libpq.so.5", "libstdc++.so.6", "libm.so.6", "libgcc_s.so.1", "libc.so.6"}


# --- environment ---------------------------------------------------------------------------------


def os_release() -> dict:
    data = {}
    for line in Path("/etc/os-release").read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            data[key] = value.strip().strip('"')
    return data


def dpkg_version(package: str) -> str | None:
    try:
        return output(["dpkg-query", "-W", "-f=${Version}", package]) or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def check_environment(profile: dict) -> dict:
    """Refuse to build anywhere but the profile's environment; return what was found."""
    target, build = profile["target"], profile["build"]
    rel = os_release()
    arch = output(["dpkg", "--print-architecture"])
    problems = []
    if rel.get("ID") != target["os"] or rel.get("VERSION_ID") != target["os_version"]:
        problems.append(f"OS is {rel.get('ID')} {rel.get('VERSION_ID')}, profile needs {target['os']} {target['os_version']}")
    if arch != target["debian_arch"]:
        problems.append(f"architecture is {arch}, profile needs {target['debian_arch']}")
    cxx = shutil.which(build["cxx"])
    if not cxx:
        problems.append(f"compiler {build['cxx']} is not installed")
    else:
        major = output([cxx, "-dumpversion"]).split(".")[0]
        if int(major) != build["compiler_major"]:
            problems.append(f"{build['cxx']} is version {major}, profile needs {build['compiler_major']}")
    for tool in ("cmake", "ninja", "zstd", "nm", "readelf"):
        if not shutil.which(tool):
            problems.append(f"{tool} is not installed")
    if not dpkg_version(profile["libpq"]["build_package"]):
        problems.append(f"{profile['libpq']['build_package']} is not installed")
    if problems:
        raise SdkError("build environment does not match profile " + profile["id"] + ":\n  - " +
                       "\n  - ".join(problems) + "\nRun the build inside ./ci/run-ci (ci/Containerfile).")
    glibc = platform.libc_ver()[1]
    return {
        "os": rel.get("PRETTY_NAME"),
        "os_version_id": rel.get("VERSION_ID"),
        "arch": arch,
        "glibc": glibc,
        "compiler": output([cxx, "--version"]).splitlines()[0],  # type: ignore[list-item]
        "compiler_path": cxx,
        "cmake": output(["cmake", "--version"]).splitlines()[0].split()[-1],
        "ninja": output(["ninja", "--version"]),
        "packages": {p: dpkg_version(p) for p in ("libc6", "libstdc++6", "libpq-dev", "libpq5", "binutils",
                                                   build["cxx"]) if dpkg_version(p)},
    }


# --- compile -------------------------------------------------------------------------------------


def cmake_args(profile: dict, source: Path, builddir: Path, version: str, *, tests: bool) -> list[str]:
    build = profile["build"]
    flags = list(build["cxx_flags"]) + [
        # Reproducible objects without build-machine paths in them.
        f"-ffile-prefix-map={source}=libpqxx-{version}",
        f"-ffile-prefix-map={builddir}=.",
    ]
    args = [
        "-G", "Ninja",
        f"-DCMAKE_BUILD_TYPE={build['build_type']}",
        f"-DCMAKE_CXX_COMPILER={build['cxx']}",
        f"-DCMAKE_CXX_STANDARD={build['cxx_std']}",
        f"-DCMAKE_CXX_FLAGS={' '.join(flags)}",
        "-DBUILD_SHARED_LIBS=OFF",
        f"-DCMAKE_POSITION_INDEPENDENT_CODE={'ON' if build['pic'] else 'OFF'}",
        f"-DCMAKE_INTERPROCEDURAL_OPTIMIZATION={'ON' if build['lto'] else 'OFF'}",
        "-DBUILD_DOC=OFF",
        "-DBUILD_EXAMPLES=OFF",
        "-DBUILD_TOOLS=OFF",
        f"-DBUILD_TEST={'ON' if tests else 'OFF'}",
        "-DCMAKE_INSTALL_LIBDIR=lib",
        "-DCMAKE_INSTALL_INCLUDEDIR=include",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",  # the manifest records one exact compile command
    ]
    # ccache only when its cache is really there (ci/run-ci mounts it with CI_CACHE_NAMES=ccache;
    # the image sets CCACHE_DIR either way).
    ccache_dir = os.environ.get("CCACHE_DIR", "")
    if ccache_dir and os.path.isdir(ccache_dir) and os.access(ccache_dir, os.W_OK) and shutil.which("ccache"):
        args.append("-DCMAKE_CXX_COMPILER_LAUNCHER=ccache")
    return args


def jobs(requested: int | None) -> int:
    if requested:
        return requested
    env = os.environ.get("PQXX_JOBS", "")
    return int(env) if env.isdigit() else (os.cpu_count() or 2)


def compile_library(profile: dict, source: Path, version: str, *, tests: bool, njobs: int) -> Path:
    builddir = COMPILE / profile["id"]
    if builddir.exists():
        shutil.rmtree(builddir)
    builddir.mkdir(parents=True)
    run(["cmake", "-S", source, "-B", builddir, *cmake_args(profile, source, builddir, version, tests=tests)])
    started = time.monotonic()
    run(["cmake", "--build", builddir, "--parallel", str(njobs)])
    log(f"compiled in {time.monotonic() - started:.0f}s with {njobs} jobs")
    return builddir


def run_upstream_tests(builddir: Path) -> dict:
    """Upstream's own test suite (test/runner), linked against the static archive just built, run
    against the PostgreSQL the environment points at (tests/pg-sandbox provides a throwaway one)."""
    if not (os.environ.get("PGHOST") or os.environ.get("PGDATABASE")):
        raise SdkError("upstream tests need a PostgreSQL server: wrap the command in tests/pg-sandbox")
    runner = builddir / "test" / "runner"
    started = time.monotonic()
    result = run([runner], cwd=builddir / "test", capture=True, check=False)
    tail = (result.stdout or "").strip().splitlines()[-5:]
    print("\n".join(tail))
    if result.returncode != 0:
        print(result.stdout[-20000:] if result.stdout else "", result.stderr[-5000:] if result.stderr else "")
        raise SdkError(f"upstream test suite failed ({result.returncode})")
    return {"command": "test/runner", "result": "passed", "seconds": round(time.monotonic() - started),
            "summary": tail[-1] if tail else ""}


def compile_command(builddir: Path) -> str:
    """The exact compiler invocation for one library object, for the manifest."""
    entries = json.loads((builddir / "compile_commands.json").read_text())
    for entry in entries:
        if "/src/connection.cxx" in entry["file"]:
            command = entry.get("command") or " ".join(entry["arguments"])
            return command.replace(str(builddir), "<build>").replace(str(Path(entry["file"]).parents[1]), "<source>")
    raise SdkError("compile_commands.json has no src/connection.cxx entry")


# --- SDK assembly --------------------------------------------------------------------------------


def render_pc(version: SdkVersion, profile: dict, *, prefix: str = "${pcfiledir}/../..") -> str:
    """pkg-config metadata for the SDK.

    - prefix is derived from the .pc file's own location, so the SDK works wherever it is extracted
      (the Debian package installs a copy with a fixed prefix instead).
    - Libs names the packaged archive by path, so no other libpqxx (system libpqxx.so, libpqxx.a)
      can be picked up by a library search.
    - libpq is linked as `-l:libpq.so.5` (its SONAME), not `Requires: libpq` / `-lpq`: with
      `dependency(..., static: true)` Meson runs pkg-config --static and resolves -l flags to static
      archives, which would bundle /usr/lib/<triplet>/libpq.a and its private dependencies. The
      SONAME form always binds the distribution's shared libpq and needs no libpq-dev to link.
    """
    upstream = version.upstream
    soname = profile["libpq"]["soname"]
    return (
        f"# libpqxx {upstream} static SDK {version.sdk} ({profile['id']}), Vaulthalla distribution.\n"
        "# Relocatable: paths derive from this file's location.\n"
        f"prefix={prefix}\n"
        "libdir=${prefix}/lib\n"
        "includedir=${prefix}/include\n"
        "\n"
        f"Name: {PC_NAME}\n"
        f"Description: libpqxx {upstream} C++ client API for PostgreSQL (static, {profile['id']})\n"
        f"URL: https://github.com/jtv/libpqxx\n"
        f"Version: {upstream}\n"
        f"Libs: ${{libdir}}/libpqxx.a -l:{soname}\n"
        "Cflags: -I${includedir}\n"
    )


NOTICE = """\
# libpqxx static SDK — notices

This SDK contains libpqxx {upstream}, compiled without modification{patch_note} from the official
release published at {repository} (tag {tag}, commit {commit}).

libpqxx is Copyright (c) 2000-2026 Jeroen T. Vermeulen and contributors, distributed under the
BSD 3-Clause license: see licenses/libpqxx/COPYING. AUTHORS lists the contributors.

The archive links dynamically against the system PostgreSQL client library (libpq, PostgreSQL
License), the C library and the C++ standard library; none of them are included here.

Packaged by Vaulthalla (https://github.com/vaulthalla/libpqxx). manifest.json records the exact
upstream source, build configuration and compatibility requirements.
"""


def assemble(profile: dict, version: SdkVersion, source: Path, builddir: Path, stage: Path) -> Path:
    names = dist_names(version, profile["id"])
    root = SDK / profile["id"] / names["top"]
    if root.parent.exists():
        shutil.rmtree(root.parent)
    (root / "lib" / "pkgconfig").mkdir(parents=True)

    # Headers: exactly what upstream's CMake install produced. The generated configuration
    # header(s) are those not in the source tree (8.x: pqxx/internal/config.h; 7.x:
    # pqxx/config-public-compiler.h).
    shutil.copytree(stage / "include" / "pqxx", root / "include" / "pqxx")
    if not generated_headers(root, source):
        raise SdkError("the staged headers contain no generated configuration header")

    archives = sorted(p for p in (stage / "lib").glob("libpqxx*.a") if not p.is_symlink())
    if len(archives) != 1:
        raise SdkError(f"expected one static archive in the stage, found {[p.name for p in archives]}")
    if any(p.suffix == ".so" or ".so." in p.name for p in (stage / "lib").iterdir()):
        raise SdkError("the stage contains a shared library; BUILD_SHARED_LIBS was not honoured")
    shutil.copyfile(archives[0], root / "lib" / "libpqxx.a")
    (root / "lib" / "pkgconfig" / f"{PC_NAME}.pc").write_text(render_pc(version, profile))

    lic = root / "licenses" / "libpqxx"
    lic.mkdir(parents=True)
    for name in ("COPYING", "AUTHORS"):
        shutil.copyfile(source / name, lic / name)
    upstream = load_lock()["upstream"]
    patches = patch_files()
    (root / "licenses" / "NOTICE.md").write_text(NOTICE.format(
        upstream=version.upstream, repository=upstream["repository"], tag=upstream["tag"], commit=upstream["commit"],
        patch_note=f" except for {len(patches)} documented patch(es)" if patches else ""))
    if patches:
        (root / "licenses" / "patches").mkdir()
        for patch in patches:
            shutil.copyfile(patch, root / "licenses" / "patches" / patch.name)
    for path in root.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    root.chmod(0o755)
    return root


def _symbol_versions(binary: Path) -> dict[str, str]:
    """Highest version required per versioned namespace (GLIBC, GLIBCXX, CXXABI, GCC)."""
    best: dict[str, tuple] = {}
    text = output(["objdump", "-T", binary])
    for prefix, ver in re.findall(r"\b(GLIBCXX|GLIBC|CXXABI|GCC)_([0-9][0-9.]*)\b", text):
        key = tuple(int(p) for p in ver.split(".") if p)
        if prefix not in best or key > best[prefix]:
            best[prefix] = key
    return {k: f"{k}_{'.'.join(map(str, v))}" for k, v in sorted(best.items())}


def _dpkg_cmp(a: str, b: str) -> int:
    if a == b:
        return 0
    return -1 if subprocess.run(["dpkg", "--compare-versions", a, "lt", b]).returncode == 0 else 1


def libpq_requirements(archive: Path, profile: dict, source: Path) -> dict:
    """libpq functions the archive calls and, from libpq5's dpkg symbols file, the oldest libpq5
    providing all of them."""
    undefined = set()
    for line in output(["nm", "-u", "--format=just-symbols", archive]).splitlines():
        name = line.strip()
        if re.match(r"^(PQ|pg_|lo_)\w+$", name):
            undefined.add(name)
    arch = profile["target"]["debian_arch"]
    symbols_file = Path(f"/var/lib/dpkg/info/{profile['libpq']['runtime_package']}:{arch}.symbols")
    minimum = None
    missing = []
    if symbols_file.is_file():
        table = {}
        for line in symbols_file.read_text().splitlines():
            match = re.match(r"^\s+(\S+)@\S+\s+(\S+)", line)
            if match:
                table[match.group(1)] = match.group(2)
        versions = []
        for name in sorted(undefined):
            if name in table:
                versions.append(table[name])
            else:
                missing.append(name)
        if versions:
            minimum = max(versions, key=functools.cmp_to_key(_dpkg_cmp))
    if missing:
        raise SdkError(f"libpq symbols not exported by {profile['libpq']['runtime_package']}: {missing}")
    # Symbol presence is only a floor; upstream also states the libpq release it supports.
    upstream_min = json.loads((source / "requirements.json").read_text()).get("libpq")
    return {"soname": profile["libpq"]["soname"], "package": profile["libpq"]["runtime_package"],
            "min_version": upstream_min, "min_version_by_symbols": minimum, "functions": sorted(undefined)}


def measure(root: Path, profile: dict, workdir: Path, source: Path) -> dict:
    """Facts about the archive a consumer depends on, measured rather than assumed."""
    archive = root / "lib" / "libpqxx.a"
    workdir.mkdir(parents=True, exist_ok=True)
    data = archive.read_bytes()
    for needle in (str(ROOT).encode(), b"/release/build/", b"/workspace/"):
        if needle in data:
            raise SdkError(f"libpqxx.a contains the build-machine path {needle.decode()!r}")

    # Link the *whole* archive into a throwaway shared object: proves it is PIC, that its only
    # external dependencies are libpq and the C/C++ runtime, and yields the symbol versions.
    probe = workdir / "whole-archive-probe.so"
    run([profile["build"]["cxx"], "-shared", "-o", probe, "-Wl,--no-undefined", "-Wl,--whole-archive", archive,
         "-Wl,--no-whole-archive", f"-l:{profile['libpq']['soname']}"])
    needed = re.findall(r"\(NEEDED\)\s+Shared library: \[([^\]]+)\]", output(["readelf", "-d", probe]))
    unexpected = sorted(set(needed) - ALLOWED_NEEDED)
    if unexpected:
        raise SdkError(f"the archive pulls in unexpected shared libraries: {unexpected}")
    versions = _symbol_versions(probe)
    glibc_need = versions.get("GLIBC", "GLIBC_0").split("_", 1)[1]
    if tuple(map(int, glibc_need.split("."))) > tuple(map(int, profile["target"]["glibc"].split("."))):
        raise SdkError(f"archive needs {versions['GLIBC']}, above the profile baseline {profile['target']['glibc']}")

    members = output(["ar", "t", archive]).splitlines()
    defined = output(["nm", "--defined-only", "--format=just-symbols", archive])
    if profile["stdlib"]["cxx11_abi"] and "__cxx11" not in defined:
        raise SdkError("no __cxx11 symbols: the archive was not built with the C++11 libstdc++ ABI")
    objtype = output(["file", "-b", workdir / "whole-archive-probe.so"]) if shutil.which("file") else ""
    return {
        "archive_members": len(members),
        "needed_shared_libraries": sorted(needed),
        "symbol_versions": versions,
        "libpq": libpq_requirements(archive, profile, source),
        "probe": objtype.split(",")[0:2],
    }


def generated_headers(root: Path, source: Path) -> list[str]:
    include = root / "include"
    return sorted(p.relative_to(include).as_posix() for p in (include / "pqxx").rglob("*")
                  if p.is_file() and not (source / "include" / p.relative_to(include)).is_file())


def config_macros(root: Path, generated: list[str]) -> dict:
    macros = {}
    text = "\n".join((root / "include" / rel).read_text() for rel in generated)
    for line in text.splitlines():
        if m := re.match(r"#define (PQXX_\w+)", line):
            macros[m.group(1)] = True
        elif m := re.match(r"/\* #undef (PQXX_\w+) \*/", line):
            macros[m.group(1)] = False
    return dict(sorted(macros.items()))


def file_digests(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha256_file(p) for p in sorted(root.rglob("*"))
            if p.is_file() and p.name != "manifest.json"}


def write_manifest(root: Path, profile: dict, version: SdkVersion, env: dict, builddir: Path,
                   measured: dict, tests: dict | None, njobs: int, source: Path) -> dict:
    lock = load_lock()
    generated = generated_headers(root, source)
    build = profile["build"]
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "name": SDK_NAME,
        "sdk_version": version.sdk,
        "profile": profile["id"],
        "pkg_config": {"name": PC_NAME, "file": f"lib/pkgconfig/{PC_NAME}.pc", "relocatable": True},
        "upstream": lock["upstream"],
        "target": profile["target"],
        "build": {
            "system": "upstream CMake + Ninja",
            "build_type": build["build_type"],
            "cxx_std": f"c++{build['cxx_std']}",
            "shared": False,
            "pic": build["pic"],
            "lto": build["lto"],
            "cxx_flags": build["cxx_flags"],
            "compile_command": compile_command(builddir),
            "cmake_args": [a for a in cmake_args(profile, Path("<source>"), Path("."), version.upstream, tests=False)
                           if not a.startswith("-DCMAKE_CXX_COMPILER_LAUNCHER")],
            "patches": [{"file": p.name, "sha256": sha256_file(p)} for p in patch_files()],
            "environment": env,
            "image": build["image"],
            "jobs": njobs,
            "repository_commit": git_head(),
            "upstream_tests": tests,
        },
        "stdlib": profile["stdlib"],
        "generated_headers": generated,
        "config_h": config_macros(root, generated),
        "requirements": {
            "glibc": measured["symbol_versions"].get("GLIBC"),
            "libstdcxx": measured["symbol_versions"].get("GLIBCXX"),
            "cxxabi": measured["symbol_versions"].get("CXXABI"),
            "libgcc": measured["symbol_versions"].get("GCC"),
            "needed_shared_libraries": measured["needed_shared_libraries"],
            "libpq": measured["libpq"],
            "consumers": profile["consumers"],
        },
        "archive_members": measured["archive_members"],
        "files": file_digests(root),
    }
    write_json(root / "manifest.json", manifest)
    (root / "manifest.json").chmod(0o644)
    return manifest


def make_archive(root: Path, target: Path, epoch: int) -> None:
    """A deterministic tar (sorted, numeric root ownership, fixed mtimes) compressed with zstd."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        paths = [root] + sorted(root.rglob("*"))
        for path in paths:
            arcname = Path(root.name) / path.relative_to(root) if path != root else Path(root.name)
            info = tar.gettarinfo(str(path), arcname.as_posix())
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = epoch
            info.mode = 0o755 if path.is_dir() else 0o644
            if path.is_file():
                with path.open("rb") as handle:
                    tar.addfile(info, handle)
            else:
                tar.addfile(info)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".part")
    subprocess.run(["zstd", "-19", "-T1", "-q", "-f", "-o", str(tmp)], input=buffer.getvalue(), check=True)
    tmp.replace(target)


def package(root: Path, manifest: dict, version: SdkVersion, profile_id: str) -> dict:
    names = dist_names(version, profile_id)
    DIST.mkdir(parents=True, exist_ok=True)
    for name in names.values():
        (DIST / name).unlink(missing_ok=True)
    archive = DIST / names["archive"]
    make_archive(root, archive, source_date_epoch(load_lock()))
    digest = sha256_file(archive)
    (DIST / names["sha256"]).write_text(f"{digest}  {names['archive']}\n")
    sidecar = dict(manifest)
    sidecar["archive"] = {"file": names["archive"], "sha256": digest, "size": archive.stat().st_size,
                          "top_dir": names["top"], "compression": "zstd"}
    write_json(DIST / names["manifest"], sidecar)
    log(f"SDK archive {archive.relative_to(ROOT)} ({archive.stat().st_size // 1024} KiB, sha256 {digest})")
    return sidecar["archive"]


def build(profile_id: str, *, tests: bool, njobs: int | None, offline: bool) -> dict:
    profile = load_profile(profile_id)
    if profile.get("status") != "supported":
        raise SdkError(f"profile {profile_id} is {profile.get('status')}, not supported")
    version = release_version()
    upstream_mod.check_version_matches_lock(version)
    env = check_environment(profile)
    n = jobs(njobs)
    source = upstream_mod.fetch_source(offline=offline)
    builddir = compile_library(profile, source, version.upstream, tests=tests, njobs=n)
    test_result = run_upstream_tests(builddir) if tests else None

    stage = STAGE / profile_id
    if stage.exists():
        shutil.rmtree(stage)
    run(["cmake", "--install", builddir, "--prefix", stage])
    root = assemble(profile, version, source, builddir, stage)
    measured = measure(root, profile, METADATA / profile_id, source)
    manifest = write_manifest(root, profile, version, env, builddir, measured, test_result, n, source)
    archive = package(root, manifest, version, profile_id)
    write_json(METADATA / profile_id / "build.json", {"sdk": str(root.relative_to(ROOT)), "archive": archive,
                                                     "requirements": manifest["requirements"]})
    return {"sdk_dir": str(root), "archive": str(DIST / archive["file"]), "sha256": archive["sha256"]}

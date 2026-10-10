"""Unit tests for tools/pqxx-sdk and tools/libpqxx-sdk-fetch (standard library only, no network,
no compiler): python3 -m unittest discover -s tests/unit -t ."""

from __future__ import annotations

import hashlib
import importlib.machinery
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from pqxxsdk import build, common, inspect, upstream  # noqa: E402


def load_fetch_script():
    loader = importlib.machinery.SourceFileLoader("libpqxx_sdk_fetch", str(REPO / "tools" / "libpqxx-sdk-fetch"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class VersionTests(unittest.TestCase):
    def test_version_is_upstream_revision(self):
        v = common.parse_sdk_version("8.0.2-1\n")
        self.assertEqual((v.upstream, v.revision, v.sdk), ("8.0.2", 1, "8.0.2-1"))
        self.assertEqual(common.parse_sdk_version("7.10.7-12").sdk, "7.10.7-12")

    def test_invalid_versions(self):
        for raw in ("8.0.201", "8.0.2", "8.0.2-0", "8.0.2-01", "08.0.2-1", "8.0.2~rc1-1", "1:8.0.2-1", "v8.0.2-1"):
            with self.subTest(raw), self.assertRaises(common.SdkError):
                common.parse_sdk_version(raw)

    def test_repository_version_matches_lock(self):
        upstream.check_version_matches_lock()

    def test_release_toml_uses_the_debian_upstream_policy(self):
        with (REPO / "release.toml").open("rb") as handle:
            config = tomllib.load(handle)
        self.assertEqual(config["version"]["policy"], "debian-upstream")
        self.assertEqual(common.release_version().sdk, (REPO / "VERSION").read_text().strip())


class DiscoveryTests(unittest.TestCase):
    PAGE = [
        {"tag_name": "8.0.2", "prerelease": False, "draft": False, "published_at": "2026-07-18T00:00:00Z"},
        {"tag_name": "7.10.7", "prerelease": False, "draft": False, "published_at": "2026-08-13T00:00:00Z"},
        {"tag_name": "8.1.0-rc1", "prerelease": True, "draft": False, "published_at": "2026-09-01T00:00:00Z"},
        {"tag_name": "9.0.0", "prerelease": False, "draft": True, "published_at": None},
        {"tag_name": "8.0.10", "prerelease": False, "draft": False, "published_at": "2026-06-01T00:00:00Z"},
        {"tag_name": "nightly", "prerelease": False, "draft": False, "published_at": "2026-06-01T00:00:00Z"},
    ]

    def test_newest_by_version_not_date_and_no_prereleases(self):
        with mock.patch.object(upstream, "_get_json", return_value=(self.PAGE, None)):
            releases = upstream.stable_releases()
        self.assertEqual([r["version"] for r in releases], ["8.0.10", "8.0.2", "7.10.7"])

    def test_discover_reports_newer(self):
        with mock.patch.object(upstream, "_get_json", return_value=(self.PAGE, None)), \
             mock.patch.object(upstream, "load_lock", return_value={"upstream": {"version": "8.0.2"}}):
            result = upstream.discover()
        self.assertTrue(result["update_available"])
        self.assertEqual(result["latest"], "8.0.10")
        self.assertEqual(result["newer"], ["8.0.10"])

    def test_pagination_link(self):
        link = '<https://api.github.com/x?page=2>; rel="next", <https://api.github.com/x?page=5>; rel="last"'
        self.assertEqual(upstream._next_link(link), "https://api.github.com/x?page=2")
        self.assertIsNone(upstream._next_link(None))


class PkgConfigTests(unittest.TestCase):
    def setUp(self):
        self.profile = common.load_profile("ubuntu-24.04-amd64")
        self.version = common.SdkVersion("8.0.2", 1)

    def test_relocatable_and_explicit(self):
        pc = build.render_pc(self.version, self.profile)
        self.assertIn("prefix=${pcfiledir}/../..\n", pc)
        self.assertIn("Libs: ${libdir}/libpqxx.a -l:libpq.so.5\n", pc)
        self.assertIn("Version: 8.0.2\n", pc)
        self.assertNotRegex(pc, r"(?m)^Requires")
        self.assertNotRegex(pc, r"-lpqxx|-lpq\b")

    def test_fixed_prefix_for_debian(self):
        pc = build.render_pc(self.version, self.profile, prefix="/usr/lib/x86_64-linux-gnu/libpqxx-vh")
        self.assertIn("prefix=/usr/lib/x86_64-linux-gnu/libpqxx-vh\n", pc)

    def test_pkg_config_resolves_relative_to_file(self):
        if not shutil.which("pkg-config"):
            self.skipTest("pkg-config not installed")
        with tempfile.TemporaryDirectory() as tmp:
            pc_dir = Path(tmp, "some where", "sdk", "lib", "pkgconfig")
            pc_dir.mkdir(parents=True)
            (pc_dir / "libpqxx-vh.pc").write_text(build.render_pc(self.version, self.profile))
            out = subprocess.run(["pkg-config", "--static", "--libs", "libpqxx-vh"], capture_output=True, text=True,
                                 env={"PKG_CONFIG_LIBDIR": str(pc_dir), "PATH": "/usr/bin:/bin"}, check=True).stdout
            # pkgconf escapes spaces in paths; compare normalised.
            first = out.split(" -l:")[0].replace("\\ ", " ").strip()
            self.assertEqual(Path(first).resolve(), (pc_dir.parent / "libpqxx.a").resolve())


class CMakeArgsTests(unittest.TestCase):
    def test_upstream_options_only(self):
        profile = common.load_profile("ubuntu-24.04-amd64")
        args = build.cmake_args(profile, Path("/src"), Path("/b"), "8.0.2", tests=False)
        joined = " ".join(args)
        for expected in ("-DBUILD_SHARED_LIBS=OFF", "-DCMAKE_POSITION_INDEPENDENT_CODE=ON",
                         "-DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF", "-DCMAKE_CXX_STANDARD=20", "-DBUILD_DOC=OFF",
                         "-DBUILD_TEST=OFF", "-DCMAKE_BUILD_TYPE=Release", "-march=x86-64", "-mtune=generic",
                         "-ffile-prefix-map=/src=libpqxx-8.0.2"):
            self.assertIn(expected, joined)
        self.assertNotIn("native", joined)
        self.assertNotIn("-flto", joined)


class FetchScriptTests(unittest.TestCase):
    def setUp(self):
        self.fetch = load_fetch_script()
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def pin(self, text: str) -> Path:
        path = self.tmp / "pin.toml"
        path.write_text(text)
        return path

    def test_valid_pin_and_url(self):
        pin = self.fetch.load_pin(self.pin(
            'sdk_version = "8.0.2-1"\nprofile = "ubuntu-24.04-amd64"\nsha256 = "' + "a" * 64 + '"\n'
            'base_url = "https://repo.valkyrianlabs.com/repository/cpp-sdk/"\n'), None)
        self.assertEqual(self.fetch.artifact_url(pin),
                         "https://repo.valkyrianlabs.com/repository/cpp-sdk/libpqxx/8.0.2-1/ubuntu-24.04-amd64/libpqxx-sdk.tar.zst")

    def test_rejects_unpinned_or_insecure(self):
        bad = [
            'sdk_version = "latest"\nprofile = "ubuntu-24.04-amd64"\nsha256 = "' + "a" * 64 + '"\nbase_url = "https://x"\n',
            'sdk_version = "8.0.2-1"\nprofile = "ubuntu-24.04-amd64"\nbase_url = "https://x"\n',
            'sdk_version = "8.0.2-1"\nprofile = "ubuntu-24.04-amd64"\nsha256 = "' + "a" * 64 + '"\nbase_url = "http://x"\n',
        ]
        for text in bad:
            with self.assertRaises(self.fetch.Failure):
                self.fetch.load_pin(self.pin(text), None)

    def test_host_check(self):
        manifest = {"sdk_version": "8.0.2-1", "profile": "p", "target": {"os": "ubuntu", "os_version": "24.04",
                    "debian_arch": "amd64", "gnu_triplet": "x86_64-linux-gnu"},
                    "requirements": {"glibc": "GLIBC_2.32", "libpq": {"soname": "libpq.so.5", "package": "libpq5"}}}
        facts = {"os": "debian", "version": "12", "arch": "amd64", "glibc": "2.36"}
        with mock.patch.object(self.fetch, "host_facts", return_value=facts):
            with self.assertRaisesRegex(self.fetch.Failure, "targets ubuntu"):
                self.fetch.check_host(manifest)
        facts = {"os": "ubuntu", "version": "22.04", "arch": "arm64", "glibc": "2.35"}
        with mock.patch.object(self.fetch, "host_facts", return_value=facts):
            with self.assertRaisesRegex(self.fetch.Failure, "24.04 or newer"):
                self.fetch.check_host(manifest)


class InspectTests(unittest.TestCase):
    """A synthetic SDK archive (no compiler) exercises the inspector's refusals."""

    def make_sdk(self, tmp: Path, mutate=None) -> Path:
        version = common.SdkVersion("8.0.2", 1)
        profile = common.load_profile("ubuntu-24.04-amd64")
        root = tmp / f"libpqxx-sdk-{version.sdk}-{profile['id']}"
        files = {
            "include/pqxx/pqxx": "#include \"pqxx/connection.hxx\"\n",
            "include/pqxx/connection.hxx": "#include \"pqxx/internal/config.h\"\n",
            "include/pqxx/internal/config.h": "#define PQXX_HAVE_POLL\n/* #undef PQXX_HAVE_STACKTRACE */\n",
            "lib/libpqxx.a": "!<arch>\n",
            "lib/pkgconfig/libpqxx-vh.pc": build.render_pc(version, profile),
            "licenses/libpqxx/COPYING": "BSD\n",
            "licenses/NOTICE.md": "notice\n",
        }
        if mutate:
            mutate(files)
        for rel, text in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
        manifest = {"schema": 1, "name": "libpqxx-sdk", "sdk_version": version.sdk, "profile": profile["id"],
                    "upstream": {"version": "8.0.2", "commit": "c" * 40},
                    "generated_headers": ["pqxx/internal/config.h"],
                    "config_h": {"PQXX_HAVE_POLL": True, "PQXX_HAVE_STACKTRACE": False},
                    "files": {rel: hashlib.sha256(text.encode()).hexdigest() for rel, text in files.items()}}
        (root / "manifest.json").write_text(json.dumps(manifest))
        archive = tmp / "sdk.tar.zst"
        build.make_archive(root, archive, 1_700_000_000)
        return archive

    def setUp(self):
        if not shutil.which("zstd"):
            self.skipTest("zstd not installed")
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_good_archive_passes_and_is_deterministic(self):
        archive = self.make_sdk(self.tmp / "a")
        report = inspect.inspect(archive)
        self.assertEqual(report["headers"], 3)
        again = self.make_sdk(self.tmp / "b")
        self.assertEqual(common.sha256_file(archive), common.sha256_file(again))

    def test_refusals(self):
        cases = {
            "includes pqxx/missing.hxx": lambda f: f.__setitem__("include/pqxx/pqxx", "#include <pqxx/missing.hxx>\n"),
            "forbidden member lib/libpqxx.so": lambda f: f.__setitem__("lib/libpqxx.so", "x"),
            "absolute path": lambda f: f.__setitem__("lib/pkgconfig/libpqxx-vh.pc",
                                                     f["lib/pkgconfig/libpqxx-vh.pc"].replace("${pcfiledir}/../..", "/opt/x")),
            "SONAME": lambda f: f.__setitem__("lib/pkgconfig/libpqxx-vh.pc",
                                              f["lib/pkgconfig/libpqxx-vh.pc"] + "Requires.private: libpq\n"),
            "missing licenses/libpqxx/COPYING": lambda f: f.pop("licenses/libpqxx/COPYING"),
            "generated header include/pqxx/internal/config.h is missing":
                lambda f: (f.pop("include/pqxx/internal/config.h"),
                           f.__setitem__("include/pqxx/connection.hxx", "\n")),
            "PQXX_HAVE_POLL defined=False": lambda f: f.__setitem__("include/pqxx/internal/config.h", "\n"),
        }
        for i, (expected, mutate) in enumerate(cases.items()):
            with self.subTest(expected):
                archive = self.make_sdk(self.tmp / str(i), mutate)
                with self.assertRaisesRegex(common.SdkError, re.escape(expected)):
                    inspect.inspect(archive)

    def test_file_not_in_manifest_is_refused(self):
        archive = self.make_sdk(self.tmp / "x")
        raw = subprocess.run(["zstd", "-dc", str(archive)], capture_output=True, check=True).stdout
        work = self.tmp / "unpacked"
        work.mkdir()
        subprocess.run(["tar", "-x", "-C", str(work)], input=raw, check=True)
        root = next(work.iterdir())
        (root / "lib" / "extra.txt").write_text("sneaky")
        build.make_archive(root, archive, 1)
        with self.assertRaisesRegex(common.SdkError, "lib/extra.txt"):
            inspect.inspect(archive)


class RepositoryTests(unittest.TestCase):
    def test_profile_image_matches_containerfile(self):
        profile = common.load_profile("ubuntu-24.04-amd64")
        containerfile = (REPO / "ci" / "Containerfile").read_text()
        self.assertIn(f"FROM {profile['build']['image']}", containerfile)
        self.assertIn(profile["build"]["cxx"], containerfile)
        self.assertIn(profile["libpq"]["build_package"], containerfile)

    def test_containerfile_has_debian_build_depends(self):
        control = (REPO / "debian" / "control").read_text()
        depends = re.search(r"^Build-Depends: (.*)$", control, re.MULTILINE).group(1)
        containerfile = (REPO / "ci" / "Containerfile").read_text()
        for dep in depends.split(","):
            name = dep.strip().split()[0]
            name = "debhelper" if name == "debhelper-compat" else name
            self.assertRegex(containerfile, rf"\b{re.escape(name)}\b", name)

    def test_lock_is_complete(self):
        lock = common.load_lock()["upstream"]
        self.assertRegex(lock["commit"], r"^[0-9a-f]{40}$")
        self.assertRegex(lock["tree"], r"^[0-9a-f]{40}$")
        self.assertRegex(lock["archive"]["sha256"], r"^[0-9a-f]{64}$")
        self.assertTrue(lock["archive"]["url"].startswith("https://github.com/jtv/libpqxx/archive/refs/tags/"))

    def test_generated_output_is_ignored_and_contained(self):
        ignore = (REPO / ".gitignore").read_text().split()
        self.assertIn("/release/", ignore)
        with (REPO / "release.toml").open("rb") as handle:
            config = tomllib.load(handle)
        self.assertTrue(config["release"]["build_dir"].startswith("release/"))
        self.assertTrue(config["release"]["output_dir"].startswith("release/"))
        self.assertTrue(str(common.WORK).startswith(str(REPO / "release")))

    def test_no_upstream_source_committed(self):
        try:
            tracked = subprocess.run(["git", "-C", str(REPO), "ls-files"], capture_output=True, text=True,
                                     check=True).stdout.split()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("not a git checkout")
        offenders = [p for p in tracked if (p.endswith((".cxx", ".hxx", ".a", ".tar.gz", ".tar.zst"))
                                            and not p.startswith("tests/consumer/"))
                     or p in ("CMakeLists.txt", "COPYING", "configure")]
        self.assertEqual(offenders, [])

    def test_debian_package_is_namespaced(self):
        control = (REPO / "debian" / "control").read_text()
        self.assertNotRegex(control, r"(?m)^(Provides|Conflicts|Replaces|Breaks):")
        self.assertIn("Package: libpqxx-vh-dev", control)


if __name__ == "__main__":
    unittest.main()

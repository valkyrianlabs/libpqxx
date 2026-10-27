"""The auto-release gate: only a candidate that needs no judgement is released without a maintainer."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from pqxxsdk import gate  # noqa: E402

CLEAN = {"status": "passed", "old_version": "8.0.2", "new_version": "8.0.3", "release": "8.0.3-1",
         "commit": "c" * 40, "requirements_changed": False, "build_files_changed": False}


class GateTests(unittest.TestCase):
    def test_clean_patch_release_releases(self):
        self.assertEqual(gate.blockers(CLEAN), [])

    def test_patch_release(self):
        self.assertTrue(gate.is_patch_release("8.0.2", "8.0.10"))
        for old, new in [("8.0.2", "8.1.0"), ("8.0.2", "9.0.0"), ("8.0.2", "8.0.2"), ("8.0.3", "8.0.2"),
                         ("8.0.2", "8.0.3rc1"), ("", "8.0.3")]:
            with self.subTest(old=old, new=new):
                self.assertFalse(gate.is_patch_release(old, new))

    def test_each_judgement_call_blocks(self):
        cases = {
            "failed": {"status": "failed"},
            "not a patch release": {"new_version": "8.1.0"},
            "requirements.json": {"requirements_changed": True},
            "build-system": {"build_files_changed": True},
            "VERSION": {"release": ""},
        }
        for needle, change in cases.items():
            with self.subTest(needle):
                reasons = gate.blockers({**CLEAN, **change})
                self.assertEqual(len(reasons), 1, reasons)
                self.assertIn(needle, reasons[0])

    def test_missing_fields_fail_closed(self):
        self.assertGreaterEqual(len(gate.blockers({})), 4)

    def test_github_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            result, output = Path(tmp, "result.json"), Path(tmp, "out")
            result.write_text(json.dumps(CLEAN))
            with contextlib.redirect_stdout(io.StringIO()):
                status = gate.run_gate(str(result), github_output=str(output))
            self.assertEqual(status, 0)
            self.assertEqual(output.read_text(), "release=true\nversion=8.0.3-1\nblockers<<GATE_EOF\nGATE_EOF\n")

    def test_workflow_releases_through_the_gate(self):
        workflow = (REPO / ".github/workflows/upstream.yml").read_text()
        self.assertIn("tools/pqxx-sdk gate", workflow)
        self.assertIn("vars.UPSTREAM_AUTO_RELEASE != 'false'", workflow)
        self.assertIn('gh workflow run release.yml --ref "$tag" -f publish=publish', workflow)
        self.assertIn("--match-head-commit", workflow)


if __name__ == "__main__":
    unittest.main()

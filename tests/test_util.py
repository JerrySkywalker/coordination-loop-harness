from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from coordination_loop_harness.util import (
    canonical_repo,
    canonical_scope,
    ensure_within,
    paths_overlap,
)


class PathNormalizationTests(unittest.TestCase):
    def test_windows_and_unc_paths_are_casefolded(self):
        self.assertEqual("v:/src/example", canonical_scope(r"V:\SRC\Example"))
        self.assertEqual("//server/share/repo", canonical_scope(r"\\SERVER\Share\Repo"))

    def test_windows_dot_segments_and_separators_are_canonical(self):
        self.assertEqual(
            canonical_scope(r"V:\src\scope\target"),
            canonical_scope(r"v:/SRC/scope/child/../target/"),
        )
        self.assertTrue(
            paths_overlap(
                r"V:\src\scope\target",
                r"v:/SRC/scope/target/child",
            )
        )
        self.assertFalse(
            paths_overlap(
                r"V:\src\scope\target",
                r"V:\src\scope\target-sibling",
            )
        )

    def test_posix_path_keeps_platform_semantics(self):
        raw = "/tmp/example"
        expected = str(Path(raw).resolve()) if os.name == "posix" else raw
        self.assertEqual(expected, canonical_scope(raw))

    @unittest.skipUnless(os.name == "posix", "POSIX filesystem alias test")
    def test_posix_aliases_share_resolved_identity(self):
        raw = "/tmp/example"
        resolved = str(Path(raw).resolve())
        self.assertEqual(canonical_scope(raw), canonical_scope(resolved))
        self.assertTrue(paths_overlap(raw, resolved))

    def test_reparse_or_symlink_resolution_and_escape_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            trusted = base / "trusted"
            external = base / "external"
            trusted.mkdir()
            external.mkdir()
            link = trusted / "link"
            if os.name == "nt":
                created = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link), str(external)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if created.returncode != 0:
                    self.skipTest("Windows junction creation is unavailable")
            else:
                try:
                    link.symlink_to(external, target_is_directory=True)
                except OSError as exc:
                    self.skipTest(f"directory symlink creation is unavailable: {exc}")
            self.assertEqual(
                canonical_scope(str(external)),
                canonical_scope(str(link)),
            )
            with self.assertRaisesRegex(ValueError, "must stay within"):
                ensure_within(link / "victim.json", trusted)

    def test_common_github_origins_have_one_identity(self):
        expected = "example/repo"
        self.assertEqual(expected, canonical_repo("https://github.com/Example/Repo.git"))
        self.assertEqual(expected, canonical_repo("git@github.com:Example/Repo.git"))
        self.assertEqual(expected, canonical_repo("ssh://git@github.com/Example/Repo.git"))


if __name__ == "__main__":
    unittest.main()

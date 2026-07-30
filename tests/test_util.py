from __future__ import annotations

import unittest

from coordination_loop_harness.util import canonical_repo, canonical_scope


class PathNormalizationTests(unittest.TestCase):
    def test_windows_and_unc_paths_are_casefolded(self):
        self.assertEqual("v:/src/example", canonical_scope(r"V:\SRC\Example"))
        self.assertEqual("//server/share/repo", canonical_scope(r"\\SERVER\Share\Repo"))

    def test_posix_path_keeps_platform_semantics(self):
        self.assertEqual("/tmp/example", canonical_scope("/tmp/example"))

    def test_common_github_origins_have_one_identity(self):
        expected = "example/repo"
        self.assertEqual(expected, canonical_repo("https://github.com/Example/Repo.git"))
        self.assertEqual(expected, canonical_repo("git@github.com:Example/Repo.git"))
        self.assertEqual(expected, canonical_repo("ssh://git@github.com/Example/Repo.git"))


if __name__ == "__main__":
    unittest.main()

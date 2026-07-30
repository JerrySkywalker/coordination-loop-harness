from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from coordination_loop_harness.repository import verify_repository


class RepositoryVerificationTests(unittest.TestCase):
    def repository(self, base: Path) -> tuple[Path, str]:
        repo = base / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"],
            check=True,
        )
        (repo / "file.txt").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "file.txt"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "fixture"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "remote", "add", "origin", "git@github.com:example/repo.git"],
            check=True,
        )
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", head],
            check=True,
        )
        return repo, head

    def test_offline_exact_binding_and_dirty_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, head = self.repository(Path(tmp))
            result = verify_repository(
                repo,
                expected_origin="https://github.com/example/repo",
                stable_branch="main",
                expected_sha=head,
                local_ref="refs/heads/main",
                cached_origin_ref="refs/remotes/origin/main",
                require_detached=False,
                offline=True,
            )
            self.assertTrue(result["ok"])
            self.assertFalse(result["tracked_dirty"])
            (repo / "untracked.txt").write_text("local\n", encoding="utf-8")
            dirty = verify_repository(repo, offline=True)
            self.assertEqual(["untracked.txt"], dirty["untracked"])

    def test_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, _ = self.repository(Path(tmp))
            result = verify_repository(
                repo,
                expected_origin="example/other",
                stable_branch="release",
                expected_sha="0" * 40,
                offline=True,
            )
            self.assertFalse(result["ok"])
            self.assertGreaterEqual(len(result["findings"]), 3)

    def test_offline_verification_does_not_refresh_git_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, _ = self.repository(Path(tmp))
            index = repo / ".git" / "index"
            before = hashlib.sha256(index.read_bytes()).hexdigest()
            tracked = repo / "file.txt"
            stat = tracked.stat()
            os.utime(tracked, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
            result = verify_repository(repo, offline=True)
            after = hashlib.sha256(index.read_bytes()).hexdigest()
            self.assertTrue(result["ok"])
            self.assertTrue(result["read_only"])
            self.assertEqual(before, after)

    def test_live_verification_uses_fake_gh(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, _ = self.repository(base)
            fake_gh = base / "fake-gh.py"
            fake_gh.write_text(
                "import json\n"
                "print(json.dumps({"
                "'nameWithOwner': 'example/repo', "
                "'url': 'https://github.com/example/repo'"
                "}))\n",
                encoding="utf-8",
            )
            result = verify_repository(
                repo,
                expected_origin="example/repo",
                offline=False,
                gh_command=str(fake_gh),
            )
            self.assertTrue(result["ok"])
            self.assertTrue(result["live_github_verified"])

    def test_live_verification_rejects_wrong_repository_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, _ = self.repository(base)
            fake_gh = base / "fake-gh.py"
            fake_gh.write_text(
                "import json\n"
                "print(json.dumps({"
                "'nameWithOwner': 'example/wrong', "
                "'url': 'https://github.com/example/wrong'"
                "}))\n",
                encoding="utf-8",
            )
            result = verify_repository(
                repo,
                expected_origin="example/repo",
                offline=False,
                gh_command=str(fake_gh),
            )
            self.assertFalse(result["ok"])
            self.assertFalse(result["live_github_verified"])
            self.assertIn("repository identity mismatch", "\n".join(result["findings"]))

    def test_live_verification_rejects_wrong_host_or_invalid_json(self):
        scenarios = (
            {
                "name": "wrong-host",
                "payload": json.dumps(
                    {
                        "nameWithOwner": "example/repo",
                        "url": "https://example.invalid/example/repo",
                    }
                ),
                "finding": "repository host mismatch",
            },
            {
                "name": "invalid-json",
                "payload": "not-json",
                "finding": "invalid JSON",
            },
        )
        for scenario in scenarios:
            with self.subTest(scenario=scenario["name"]), tempfile.TemporaryDirectory() as tmp:
                base = Path(tmp)
                repo, _ = self.repository(base)
                fake_gh = base / "fake-gh.py"
                fake_gh.write_text(
                    f"print({scenario['payload']!r})\n",
                    encoding="utf-8",
                )
                result = verify_repository(
                    repo,
                    expected_origin="example/repo",
                    offline=False,
                    gh_command=str(fake_gh),
                )
                self.assertFalse(result["ok"])
                self.assertFalse(result["live_github_verified"])
                self.assertIn(scenario["finding"], "\n".join(result["findings"]))

    def test_detached_worktree_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, head = self.repository(base)
            worktree = base / "detached"
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "add", "--detach", str(worktree), head],
                check=True,
                capture_output=True,
            )
            result = verify_repository(
                worktree,
                expected_sha=head,
                require_detached=True,
                offline=True,
            )
            self.assertTrue(result["ok"])
            self.assertTrue(result["detached"])

    def test_missing_gh_is_a_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, _ = self.repository(Path(tmp))
            result = verify_repository(
                repo,
                expected_origin="example/repo",
                offline=False,
                gh_command=str(Path(tmp) / "missing-gh"),
            )
            self.assertFalse(result["ok"])
            self.assertIn("live GitHub verification failed", "\n".join(result["findings"]))

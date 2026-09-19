from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coordination_loop_harness.repository import (
    verify_repository,
    verify_template_repository_provenance,
)


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

    def fake_template_gh(
        self,
        base: Path,
        *,
        target_template: str = "example/template",
        template_tree: str,
        fail_endpoint: str | None = None,
    ) -> Path:
        fake_gh = base / "fake-template-gh.py"
        fake_gh.write_text(
            "import sys\n"
            f"target_template = {target_template!r}\n"
            f"template_tree = {template_tree!r}\n"
            f"fail_endpoint = {fail_endpoint!r}\n"
            "endpoint = next((arg for arg in sys.argv if arg.startswith('repos/')), '')\n"
            "if endpoint == fail_endpoint:\n"
            "    print('synthetic API failure', file=sys.stderr)\n"
            "    raise SystemExit(1)\n"
            "if endpoint == 'repos/example/derived':\n"
            "    print(target_template)\n"
            "elif endpoint.startswith('repos/example/template/git/commits/'):\n"
            "    print(template_tree)\n"
            "else:\n"
            "    print('unexpected endpoint', file=sys.stderr)\n"
            "    raise SystemExit(2)\n",
            encoding="utf-8",
        )
        return fake_gh

    def template_provenance(
        self,
        repo: Path,
        gh_command: Path,
        template_sha: str,
    ) -> dict[str, object]:
        return verify_template_repository_provenance(
            repo,
            target_repository="example/derived",
            template_repository="example/template",
            template_exact_sha=template_sha,
            gh_command=str(gh_command),
        )

    def test_template_provenance_accepts_rest_match_without_event_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, template_sha = self.repository(base)
            tree = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            result = self.template_provenance(
                repo,
                self.fake_template_gh(base, template_tree=tree),
                template_sha,
            )
            self.assertTrue(result["ok"])
            self.assertEqual("github-rest-template_repository", result["provenance_source"])

    def test_template_provenance_rejects_empty_rest_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, template_sha = self.repository(base)
            gh = self.fake_template_gh(base, target_template="", template_tree="0" * 40)
            with self.assertRaisesRegex(ValueError, "does not identify"):
                self.template_provenance(repo, gh, template_sha)

    def test_template_provenance_rejects_rest_source_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, template_sha = self.repository(base)
            gh = self.fake_template_gh(
                base,
                target_template="example/other-template",
                template_tree="0" * 40,
            )
            with self.assertRaisesRegex(ValueError, "template source mismatch"):
                self.template_provenance(repo, gh, template_sha)

    def test_template_provenance_rejects_tree_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, template_sha = self.repository(base)
            gh = self.fake_template_gh(base, template_tree="0" * 40)
            with self.assertRaisesRegex(ValueError, "does not match"):
                self.template_provenance(repo, gh, template_sha)

    def test_template_provenance_fails_closed_on_api_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, template_sha = self.repository(base)
            gh = self.fake_template_gh(
                base,
                template_tree="0" * 40,
                fail_endpoint="repos/example/derived",
            )
            with self.assertRaisesRegex(ValueError, "REST provenance check failed"):
                self.template_provenance(repo, gh, template_sha)

    def test_non_template_with_matching_tree_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, template_sha = self.repository(base)
            tree = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            gh = self.fake_template_gh(base, target_template="", template_tree=tree)
            with self.assertRaisesRegex(ValueError, "does not identify"):
                self.template_provenance(repo, gh, template_sha)

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

    def test_v2_origin_identity_handles_transport_case_without_changing_v1(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, _ = self.repository(Path(tmp))
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "remote",
                    "set-url",
                    "origin",
                    "git@GITHUB.COM:Example/Repo.GIT",
                ],
                check=True,
            )
            legacy = verify_repository(repo, expected_origin="Example/Repo", offline=True)
            v2 = verify_repository(
                repo,
                expected_origin="Example/Repo",
                offline=True,
                repository_identity_version="v2",
            )
            self.assertFalse(legacy["ok"])
            self.assertIn("origin mismatch", "\n".join(legacy["findings"]))
            self.assertTrue(v2["ok"])

    def test_missing_cached_origin_ref_is_a_structured_finding(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, head = self.repository(Path(tmp))
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "update-ref",
                    "-d",
                    "refs/remotes/origin/main",
                ],
                check=True,
            )
            result = verify_repository(
                repo,
                expected_sha=head,
                cached_origin_ref="refs/remotes/origin/main",
                offline=True,
            )
            self.assertFalse(result["ok"])
            self.assertIn("cached origin ref unavailable", "\n".join(result["findings"]))

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

    def test_concurrent_repository_change_fails_stable_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, head = self.repository(Path(tmp))
            from coordination_loop_harness import repository as repository_module

            original_git = repository_module._git
            mutated = False

            def mutate_after_first_status(root: Path, *args: str) -> str:
                nonlocal mutated
                result = original_git(root, *args)
                if args[:2] == ("status", "--porcelain=v1") and not mutated:
                    mutated = True
                    (repo / "file.txt").write_text("concurrent\n", encoding="utf-8")
                    subprocess.run(["git", "-C", str(repo), "add", "file.txt"], check=True)
                    subprocess.run(
                        ["git", "-C", str(repo), "commit", "-m", "concurrent"],
                        check=True,
                        capture_output=True,
                    )
                return result

            with mock.patch.object(
                repository_module,
                "_git",
                side_effect=mutate_after_first_status,
            ):
                result = verify_repository(repo, expected_sha=head, offline=True)
            self.assertFalse(result["ok"])
            self.assertIn(
                "repository state changed during verification",
                "\n".join(result["findings"]),
            )

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

    def test_live_verification_detects_cached_ref_change_during_network_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo, head = self.repository(base)
            fake_gh = base / "fake-gh-mutate-ref.py"
            fake_gh.write_text(
                "import json\n"
                "import subprocess\n"
                f"repo = {str(repo)!r}\n"
                "subprocess.run([\n"
                "    'git', '-C', repo, 'update-ref', '-d',\n"
                "    'refs/remotes/origin/main'\n"
                "], check=True)\n"
                "print(json.dumps({\n"
                "    'nameWithOwner': 'example/repo',\n"
                "    'url': 'https://github.com/example/repo'\n"
                "}))\n",
                encoding="utf-8",
            )
            result = verify_repository(
                repo,
                expected_origin="example/repo",
                expected_sha=head,
                cached_origin_ref="refs/remotes/origin/main",
                offline=False,
                gh_command=str(fake_gh),
            )
            self.assertFalse(result["ok"])
            self.assertFalse(result["live_github_verified"])
            findings = "\n".join(result["findings"])
            self.assertIn("cached origin ref unavailable", findings)
            self.assertIn("repository state changed during verification", findings)
            self.assertIn("refs/remotes/origin/main", findings)

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

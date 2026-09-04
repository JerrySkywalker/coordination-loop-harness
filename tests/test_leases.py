from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from coordination_loop_harness.leases import acquire, find_overlaps, observe, release, replace
from coordination_loop_harness.util import sha256_file

ROOT = Path(__file__).resolve().parents[1]


def lease(
    lease_id: str,
    repository: str,
    generation: int = 1,
    decision_ref: str | None = None,
):
    return {
        "schema_version": "coord.repo-set-lease.v1",
        "lease_id": lease_id,
        "run_id": lease_id,
        "state": "ACTIVE",
        "generation": generation,
        "created_utc": "2026-01-01T00:00:00Z",
        "owner": "test",
        "coordination_repository": f"example/{lease_id.lower()}-train",
        "repositories": [
            {
                "repository": repository,
                "mode": "WRITE",
                "canonical_path": None,
                "worktree_root": None,
                "exact_sha": None,
            }
        ],
        "local_scopes": [],
        "infrastructure_scopes": [],
        "active_writer_repository": repository,
        "decision_ref": decision_ref,
        "released_utc": None,
        "outcome_ref": None,
    }


class LeaseTests(unittest.TestCase):
    def write(self, path: Path, data: dict):
        path.write_text(json.dumps(data), encoding="utf-8")

    def authorize(
        self,
        base: Path,
        candidate: dict,
        action: str,
        *,
        previous_decision_ref: str | None = None,
    ) -> dict:
        if not (base / "schemas").exists():
            shutil.copytree(ROOT / "schemas", base / "schemas")
            shutil.copy(ROOT / "TEMPLATE_VERSION", base / "TEMPLATE_VERSION")
        decision_id = f"DEC-{candidate['lease_id']}-{candidate['generation']}"
        directory = base / "decisions" / candidate["run_id"]
        directory.mkdir(parents=True, exist_ok=True)
        markdown = directory / f"{decision_id}.md"
        markdown.write_text(f"# {decision_id}\n", encoding="utf-8")
        decision = {
            "schema_version": "coord.decision.v2",
            "decision_id": decision_id,
            "run_id": candidate["run_id"],
            "sequence": candidate["generation"],
            "decision_type": "SCOPE_CHANGE",
            "status": "ACCEPTED",
            "issued_by": "owner",
            "issued_utc": "2026-01-01T00:00:00Z",
            "decision": "Authorize synthetic lease operation.",
            "rationale": "Fixture authorization.",
            "scope": [candidate["repositories"][0]["repository"]],
            "conditions": [],
            "authorized_actions": [action],
            "lease_id": candidate["lease_id"],
            "lease_generation": candidate["generation"],
            "previous_decision_ref": previous_decision_ref,
            "markdown_sha256": sha256_file(markdown),
        }
        decision_path = markdown.with_suffix(".json")
        self.write(decision_path, decision)
        candidate["decision_ref"] = decision_path.relative_to(base).as_posix()
        return candidate

    @staticmethod
    def git(root: Path, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=True,
            capture_output=True,
            text=True,
            shell=False,
        )
        return result.stdout.strip()

    def repository_worktree(
        self, base: Path, slug: str, *, repository: str
    ) -> tuple[Path, Path, str, str]:
        canonical = base / f"{slug}-canonical"
        writer = base / f"{slug}-writer"
        canonical.mkdir()
        self.git(canonical, "init", "-b", "main")
        self.git(canonical, "config", "user.name", "Test")
        self.git(canonical, "config", "user.email", "test@example.invalid")
        (canonical / "README.md").write_text(f"# {slug}\n", encoding="utf-8")
        self.git(canonical, "add", "README.md")
        self.git(canonical, "commit", "-m", "initial")
        self.git(canonical, "remote", "add", "origin", f"https://github.com/{repository}.git")
        branch = f"agent/{slug}"
        self.git(canonical, "worktree", "add", "-b", branch, str(writer), "HEAD")
        return canonical, writer, branch, self.git(writer, "rev-parse", "HEAD")

    @staticmethod
    def lease_v2(
        lease_id: str,
        repository: str,
        canonical: Path,
        writer: Path,
        branch: str,
        exact_sha: str,
        *,
        mode: str = "WRITE",
        coordination_repository: str = "example/program",
    ) -> dict:
        return {
            "schema_version": "coord.repo-set-lease.v2",
            "lease_id": lease_id,
            "run_id": lease_id,
            "state": "ACTIVE",
            "generation": 1,
            "created_utc": "2026-01-01T00:00:00Z",
            "heartbeat_utc": "2026-01-01T00:00:00Z",
            "expires_utc": "2026-01-01T00:10:00Z",
            "owner": f"owner-{lease_id}",
            "coordination_repository": coordination_repository,
            "repositories": [
                {
                    "repository": repository,
                    "mode": mode,
                    "canonical_path": str(canonical),
                    "worktree_root": str(writer),
                    "branch_ref": f"refs/heads/{branch}",
                    "exact_sha": exact_sha,
                }
            ],
            "local_scopes": [],
            "infrastructure_scopes": [],
            "active_writer_repository": repository if mode == "WRITE" else None,
            "decision_ref": None,
            "released_utc": None,
            "outcome_ref": None,
        }

    @staticmethod
    def coordination_self_write_lease(generation: int = 2) -> dict:
        coordination = "example/coordination"
        return {
            "schema_version": "coord.repo-set-lease.v1",
            "lease_id": "RUN-A",
            "run_id": "RUN-A",
            "state": "ACTIVE",
            "generation": generation,
            "created_utc": "2026-01-01T00:00:00Z",
            "owner": "test",
            "coordination_repository": coordination,
            "repositories": [
                {
                    "repository": coordination,
                    "mode": "WRITE",
                    "canonical_path": "V:/src/coordination",
                    "worktree_root": "V:/src/coordination",
                    "exact_sha": "a" * 40,
                },
                {
                    "repository": "example/product",
                    "mode": "READ",
                    "canonical_path": "V:/src/product",
                    "worktree_root": "V:/src/product",
                    "exact_sha": "b" * 40,
                },
            ],
            "local_scopes": [],
            "infrastructure_scopes": [],
            "active_writer_repository": coordination,
            "decision_ref": None,
            "released_utc": None,
            "outcome_ref": None,
        }

    def admit_product_then_replace_with_coordination_self_write(
        self, base: Path, locks: Path, candidate: dict | None = None
    ) -> dict:
        initial = self.authorize(base, lease("RUN-A", "example/product"), "lease:acquire")
        initial_path = base / "initial.json"
        self.write(initial_path, initial)
        acquire(initial_path, locks, repo_root=base)
        candidate = candidate or self.coordination_self_write_lease()
        self.authorize(
            base,
            candidate,
            "lease:expand",
            previous_decision_ref=initial["decision_ref"],
        )
        candidate_path = base / "replacement.json"
        self.write(candidate_path, candidate)
        return candidate

    def test_disjoint_leases_can_coexist(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            a = base / "a.json"
            b = base / "b.json"
            self.write(a, self.authorize(base, lease("RUN-A", "example/a"), "lease:acquire"))
            self.write(b, self.authorize(base, lease("RUN-B", "example/b"), "lease:acquire"))
            acquire(a, locks, repo_root=base)
            acquire(b, locks, repo_root=base)
            self.assertEqual([], find_overlaps(lease("RUN-C", "example/c"), locks))

    def test_v2_disjoint_writers_share_program_observation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            a_repo = self.repository_worktree(base, "a", repository="example/a")
            b_repo = self.repository_worktree(base, "b", repository="example/b")
            a = self.authorize(
                base,
                self.lease_v2("RUN-A", "example/a", *a_repo),
                "lease:acquire",
            )
            b = self.authorize(
                base,
                self.lease_v2("RUN-B", "example/b", *b_repo),
                "lease:acquire",
            )
            pa, pb = base / "a.json", base / "b.json"
            self.write(pa, a)
            self.write(pb, b)
            acquire(pa, locks, repo_root=base)
            acquire(pb, locks, repo_root=base)
            self.assertEqual([], find_overlaps(b, locks, excluding="RUN-B"))

    def test_v2_shared_readers_coexist_but_writer_conflicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            repo = self.repository_worktree(base, "shared", repository="example/shared")
            reader_a = self.authorize(
                base,
                self.lease_v2("READ-A", "example/shared", *repo, mode="READ"),
                "lease:acquire",
            )
            reader_b = self.authorize(
                base,
                self.lease_v2("READ-B", "example/shared", *repo, mode="READ"),
                "lease:acquire",
            )
            writer = self.authorize(
                base,
                self.lease_v2("WRITE-A", "example/shared", *repo),
                "lease:acquire",
            )
            for name, candidate in (("reader-a", reader_a), ("reader-b", reader_b)):
                self.write(base / f"{name}.json", candidate)
                acquire(base / f"{name}.json", locks, repo_root=base)
            self.write(base / "writer.json", writer)
            overlaps = find_overlaps(writer, locks)
            self.assertTrue(any(item.category == "repository" for item in overlaps))
            self.assertTrue(any(item.category == "path" for item in overlaps))
            with self.assertRaisesRegex(RuntimeError, "overlap detected"):
                acquire(base / "writer.json", locks, repo_root=base)

    def test_v2_disjoint_repositories_still_refuse_shared_mutable_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            a_repo = self.repository_worktree(base, "a", repository="example/a")
            b_repo = self.repository_worktree(base, "b", repository="example/b")
            a = self.authorize(
                base,
                self.lease_v2("RUN-A", "example/a", *a_repo),
                "lease:acquire",
            )
            b = self.authorize(
                base,
                self.lease_v2("RUN-B", "example/b", *b_repo),
                "lease:acquire",
            )
            a["local_scopes"] = [str(base / "shared-resource")]
            b["local_scopes"] = [str(base / "shared-resource" / "child")]
            pa, pb = base / "a.json", base / "b.json"
            self.write(pa, a)
            self.write(pb, b)
            acquire(pa, locks, repo_root=base)
            with self.assertRaisesRegex(RuntimeError, "path="):
                acquire(pb, locks, repo_root=base)

    def test_v2_stale_writer_blocks_until_normal_terminal_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            repo = self.repository_worktree(base, "product", repository="example/product")
            a = self.authorize(
                base,
                self.lease_v2("RUN-A", "example/product", *repo),
                "lease:acquire",
            )
            b = self.authorize(
                base,
                self.lease_v2("RUN-B", "example/product", *repo),
                "lease:acquire",
            )
            pa, pb = base / "a.json", base / "b.json"
            self.write(pa, a)
            self.write(pb, b)
            acquire(pa, locks, repo_root=base)
            stale = observe("RUN-A", locks, observed_utc="2026-01-01T00:10:01Z")
            self.assertEqual("STALE_ACTIVE", stale["ownership_status"])
            self.assertFalse(stale["automatic_reclaim"])
            with self.assertRaisesRegex(RuntimeError, "overlap detected"):
                acquire(pb, locks, repo_root=base)
            release(
                "RUN-A",
                locks,
                expected_generation=1,
                outcome_ref="runs/RUN-A/outcome.json",
            )
            self.assertEqual(
                "TERMINAL_RELEASED",
                observe("RUN-A", locks)["ownership_status"],
            )
            acquire(pb, locks, repo_root=base)

    def test_v2_malformed_terminal_blocks_only_overlapping_resource(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            locks.mkdir()
            repo = self.repository_worktree(base, "product", repository="example/product")
            malformed = self.lease_v2("MALFORMED", "example/product", *repo)
            malformed["state"] = "RELEASED"
            malformed["active_writer_repository"] = None
            (locks / "MALFORMED.lease.json").write_text(json.dumps(malformed), encoding="utf-8")
            overlapping = self.lease_v2("OVERLAP", "example/product", *repo)
            self.assertTrue(find_overlaps(overlapping, locks))

            other_repo = self.repository_worktree(base, "other", repository="example/other")
            disjoint = self.lease_v2("DISJOINT", "example/other", *other_repo)
            self.assertEqual([], find_overlaps(disjoint, locks))

    def test_v2_writer_binding_and_decision_scope_fail_before_lease_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self.repository_worktree(base, "product", repository="example/product")

            wrong_head = self.authorize(
                base,
                self.lease_v2("WRONG-HEAD", "example/product", *repo),
                "lease:acquire",
            )
            wrong_head["repositories"][0]["exact_sha"] = "0" * 40
            wrong_head_path = base / "wrong-head.json"
            self.write(wrong_head_path, wrong_head)
            wrong_head_locks = base / "wrong-head-locks"
            with self.assertRaisesRegex(ValueError, "local ref mismatch"):
                acquire(wrong_head_path, wrong_head_locks, repo_root=base)
            self.assertFalse(wrong_head_locks.exists())

            wrong_branch = self.authorize(
                base,
                self.lease_v2("WRONG-BRANCH", "example/product", *repo),
                "lease:acquire",
            )
            wrong_branch["repositories"][0]["branch_ref"] = "refs/heads/agent/other"
            wrong_branch_path = base / "wrong-branch.json"
            self.write(wrong_branch_path, wrong_branch)
            wrong_branch_locks = base / "wrong-branch-locks"
            with self.assertRaisesRegex(ValueError, "branch mismatch"):
                acquire(wrong_branch_path, wrong_branch_locks, repo_root=base)
            self.assertFalse(wrong_branch_locks.exists())

            dirty = self.authorize(
                base,
                self.lease_v2("DIRTY", "example/product", *repo),
                "lease:acquire",
            )
            dirty_path = base / "dirty.json"
            self.write(dirty_path, dirty)
            (repo[1] / "untracked.txt").write_text("local\n", encoding="utf-8")
            dirty_locks = base / "dirty-locks"
            with self.assertRaisesRegex(ValueError, "untracked files"):
                acquire(dirty_path, dirty_locks, repo_root=base)
            self.assertFalse(dirty_locks.exists())
            (repo[1] / "untracked.txt").unlink()

            wrong_scope = self.authorize(
                base,
                self.lease_v2("WRONG-SCOPE", "example/product", *repo),
                "lease:acquire",
            )
            decision_path = base / wrong_scope["decision_ref"]
            decision = json.loads(decision_path.read_text(encoding="utf-8"))
            decision["scope"] = ["example/other"]
            self.write(decision_path, decision)
            wrong_scope_path = base / "wrong-scope.json"
            self.write(wrong_scope_path, wrong_scope)
            wrong_scope_locks = base / "wrong-scope-locks"
            with self.assertRaisesRegex(ValueError, "decision scope"):
                acquire(wrong_scope_path, wrong_scope_locks, repo_root=base)
            self.assertFalse(wrong_scope_locks.exists())

    def test_v2_writer_index_lock_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = self.repository_worktree(base, "product", repository="example/product")
            candidate = self.authorize(
                base,
                self.lease_v2("LOCKED", "example/product", *repo),
                "lease:acquire",
            )
            candidate_path = base / "candidate.json"
            self.write(candidate_path, candidate)
            index_lock = Path(
                self.git(repo[1], "rev-parse", "--path-format=absolute", "--git-path", "index.lock")
            )
            index_lock.write_text("active\n", encoding="utf-8")
            locks = base / "locks"
            with self.assertRaisesRegex(ValueError, "index.lock"):
                acquire(candidate_path, locks, repo_root=base)
            self.assertFalse(locks.exists())

    def test_repository_overlap_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            a = base / "a.json"
            b = base / "b.json"
            self.write(
                a,
                self.authorize(base, lease("RUN-A", "example/product"), "lease:acquire"),
            )
            self.write(
                b,
                self.authorize(base, lease("RUN-B", "EXAMPLE/product"), "lease:acquire"),
            )
            acquire(a, locks, repo_root=base)
            with self.assertRaises(RuntimeError):
                acquire(b, locks, repo_root=base)

    def test_generation_guard_and_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            first = base / "first.json"
            second = base / "second.json"
            initial = self.authorize(
                base,
                lease("RUN-A", "example/a"),
                "lease:acquire",
            )
            self.write(first, initial)
            acquire(first, locks, repo_root=base)
            candidate = lease("RUN-A", "example/a", generation=2)
            candidate["repositories"].append(
                {
                    "repository": "example/b",
                    "mode": "READ",
                    "canonical_path": None,
                    "worktree_root": None,
                    "exact_sha": None,
                }
            )
            self.write(
                second,
                self.authorize(
                    base,
                    candidate,
                    "lease:expand",
                    previous_decision_ref=initial["decision_ref"],
                ),
            )
            replace(second, locks, expected_generation=1, repo_root=base)
            released = release(
                "RUN-A",
                locks,
                expected_generation=2,
                outcome_ref="runs/RUN-A/outcome.json",
            )
            data = json.loads(released.read_text(encoding="utf-8"))
            self.assertEqual("RELEASED", data["state"])
            self.assertEqual(3, data["generation"])

    def test_release_rejects_traversal_before_any_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            locks.mkdir()
            victim = base / "outside.lease.json"
            victim.write_text(
                json.dumps(
                    {
                        "lease_id": "outside",
                        "state": "ACTIVE",
                        "generation": 1,
                    }
                ),
                encoding="utf-8",
            )
            before = victim.read_bytes()
            with self.assertRaisesRegex(ValueError, "Invalid lease_id"):
                release(
                    "../outside",
                    locks,
                    expected_generation=1,
                    outcome_ref="runs/RUN/outcome.json",
                )
            self.assertEqual(before, victim.read_bytes())
            self.assertEqual([], list(locks.iterdir()))

    def test_release_rejects_absolute_and_sibling_prefix_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            locks.mkdir()
            for lease_id in (str(base / "victim"), r"..\locks-sibling\victim"):
                with self.subTest(lease_id=lease_id):
                    with self.assertRaisesRegex(ValueError, "Invalid lease_id"):
                        release(
                            lease_id,
                            locks,
                            expected_generation=1,
                            outcome_ref="runs/RUN/outcome.json",
                        )
            self.assertEqual([], list(locks.iterdir()))

    def test_release_requires_exact_lease_object_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            locks.mkdir()
            path = locks / "RUN-A.lease.json"
            path.write_text(
                json.dumps(
                    {
                        "lease_id": "RUN-B",
                        "state": "ACTIVE",
                        "generation": 1,
                    }
                ),
                encoding="utf-8",
            )
            before = path.read_bytes()
            with self.assertRaisesRegex(RuntimeError, "not bound"):
                release(
                    "RUN-A",
                    locks,
                    expected_generation=1,
                    outcome_ref="runs/RUN-A/outcome.json",
                )
            self.assertEqual(before, path.read_bytes())
            self.assertFalse(any(locks.glob("*.tmp")))

    def test_coordination_repository_overlap_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            a = lease("RUN-A", "example/a")
            b = lease("RUN-B", "example/b")
            b["coordination_repository"] = a["coordination_repository"]
            self.authorize(base, a, "lease:acquire")
            self.authorize(base, b, "lease:acquire")
            pa, pb = base / "a.json", base / "b.json"
            self.write(pa, a)
            self.write(pb, b)
            acquire(pa, locks, repo_root=base)
            with self.assertRaises(RuntimeError):
                acquire(pb, locks, repo_root=base)

    def test_infrastructure_scope_overlap_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            a = lease("RUN-A", "example/a")
            b = lease("RUN-B", "example/b")
            a["infrastructure_scopes"] = ["host:lax"]
            b["infrastructure_scopes"] = ["HOST:LAX"]
            self.authorize(base, a, "lease:acquire")
            self.authorize(base, b, "lease:acquire")
            pa, pb = base / "a.json", base / "b.json"
            self.write(pa, a)
            self.write(pb, b)
            acquire(pa, locks, repo_root=base)
            with self.assertRaises(RuntimeError):
                acquire(pb, locks, repo_root=base)

    def test_stale_mutex_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            locks.mkdir()
            (locks / ".repo-set-admission.mutex").mkdir()
            candidate = base / "candidate.json"
            self.write(
                candidate,
                self.authorize(base, lease("RUN-A", "example/a"), "lease:acquire"),
            )
            with self.assertRaises(RuntimeError):
                acquire(candidate, locks, repo_root=base)

    def test_replace_requires_decision_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            first = base / "first.json"
            second = base / "second.json"
            self.write(
                first,
                self.authorize(base, lease("RUN-A", "example/a"), "lease:acquire"),
            )
            acquire(first, locks, repo_root=base)
            self.write(second, lease("RUN-A", "example/a", generation=2, decision_ref=None))
            with self.assertRaises(ValueError):
                replace(second, locks, expected_generation=1, repo_root=base)

    def test_parent_child_path_overlap_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            a = lease("RUN-A", "example/a")
            b = lease("RUN-B", "example/b")
            a["local_scopes"] = ["V:/src/_worktrees/RUN-A"]
            b["local_scopes"] = ["v:/src/_worktrees/run-a/product"]
            self.authorize(base, a, "lease:acquire")
            self.authorize(base, b, "lease:acquire")
            pa, pb = base / "a.json", base / "b.json"
            self.write(pa, a)
            self.write(pb, b)
            acquire(pa, locks, repo_root=base)
            with self.assertRaises(RuntimeError):
                acquire(pb, locks, repo_root=base)

    def test_equivalent_dot_segment_path_overlap_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            a = lease("RUN-A", "example/a")
            b = lease("RUN-B", "example/b")
            a["local_scopes"] = [r"V:\src\scope\child\..\target"]
            b["local_scopes"] = ["v:/SRC/scope/target/"]
            self.authorize(base, a, "lease:acquire")
            self.authorize(base, b, "lease:acquire")
            pa, pb = base / "a.json", base / "b.json"
            self.write(pa, a)
            self.write(pb, b)
            acquire(pa, locks, repo_root=base)
            with self.assertRaises(RuntimeError):
                acquire(pb, locks, repo_root=base)

    def test_coordination_repository_conflicts_with_product_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            a = lease("RUN-A", "example/product")
            b = lease("RUN-B", "example/other")
            b["coordination_repository"] = "example/product"
            self.authorize(base, a, "lease:acquire")
            self.authorize(base, b, "lease:acquire")
            pa, pb = base / "a.json", base / "b.json"
            self.write(pa, a)
            self.write(pb, b)
            acquire(pa, locks, repo_root=base)
            with self.assertRaises(RuntimeError):
                acquire(pb, locks, repo_root=base)

    def test_coordination_self_write_replacement_passes_and_can_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            candidate = self.admit_product_then_replace_with_coordination_self_write(base, locks)
            replacement_path = base / "replacement.json"
            replace(replacement_path, locks, expected_generation=1, repo_root=base)
            released = release(
                candidate["lease_id"],
                locks,
                expected_generation=2,
                outcome_ref="runs/RUN-A/outcome.json",
            )
            stored = json.loads(released.read_text(encoding="utf-8"))
            self.assertEqual("RELEASED", stored["state"])
            self.assertIsNone(stored["active_writer_repository"])
            self.assertEqual("runs/RUN-A/outcome.json", stored["outcome_ref"])

    def test_coordination_self_write_is_rejected_on_fresh_acquire(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            candidate = self.coordination_self_write_lease(generation=1)
            self.authorize(base, candidate, "lease:acquire")
            path = base / "candidate.json"
            self.write(path, candidate)
            with self.assertRaisesRegex(ValueError, "cannot also be a product repository"):
                acquire(path, locks, repo_root=base)

    def test_coordination_self_write_rejects_a_second_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            candidate = self.coordination_self_write_lease()
            candidate["repositories"][1]["mode"] = "WRITE"
            self.admit_product_then_replace_with_coordination_self_write(base, locks, candidate)
            with self.assertRaisesRegex(ValueError, "sole WRITE"):
                replace(base / "replacement.json", locks, expected_generation=1, repo_root=base)

    def test_coordination_self_write_rejects_coordination_read_with_product_writer(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            candidate = self.coordination_self_write_lease()
            candidate["repositories"][0]["mode"] = "READ"
            candidate["repositories"][1]["mode"] = "WRITE"
            candidate["active_writer_repository"] = "example/product"
            self.admit_product_then_replace_with_coordination_self_write(base, locks, candidate)
            with self.assertRaisesRegex(ValueError, "coordination repository to be WRITE"):
                replace(base / "replacement.json", locks, expected_generation=1, repo_root=base)

    def test_coordination_self_write_rejects_active_writer_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            candidate = self.coordination_self_write_lease()
            candidate["active_writer_repository"] = "example/product"
            self.admit_product_then_replace_with_coordination_self_write(base, locks, candidate)
            with self.assertRaisesRegex(ValueError, "active_writer_repository"):
                replace(base / "replacement.json", locks, expected_generation=1, repo_root=base)

    def test_coordination_self_write_requires_exact_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            candidate = self.coordination_self_write_lease()
            candidate["repositories"][0]["exact_sha"] = None
            self.admit_product_then_replace_with_coordination_self_write(base, locks, candidate)
            with self.assertRaisesRegex(ValueError, "exact coordination repository binding"):
                replace(base / "replacement.json", locks, expected_generation=1, repo_root=base)

    def test_active_writer_must_match_single_write_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            candidate = lease("RUN-A", "example/a")
            candidate["repositories"].append(
                {
                    "repository": "example/b",
                    "mode": "WRITE",
                    "canonical_path": None,
                    "worktree_root": None,
                    "exact_sha": None,
                }
            )
            self.authorize(base, candidate, "lease:acquire")
            path = base / "candidate.json"
            self.write(path, candidate)
            with self.assertRaises(ValueError):
                acquire(path, locks, repo_root=base)

    def test_absolute_decision_reference_outside_root_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            candidate = self.authorize(
                base,
                lease("RUN-A", "example/a"),
                "lease:acquire",
            )
            candidate["decision_ref"] = str(Path(tmp).parent / "outside-decision.json")
            path = base / "candidate.json"
            self.write(path, candidate)
            with self.assertRaises(ValueError):
                acquire(path, locks, repo_root=base)


if __name__ == "__main__":
    unittest.main()

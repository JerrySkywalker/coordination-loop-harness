from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from coordination_loop_harness.leases import acquire, find_overlaps, release, replace


ROOT = Path(__file__).resolve().parents[1]


def lease(
    lease_id: str,
    repository: str,
    generation: int = 1,
    decision_ref: str | None = "AUTO",
):
    if decision_ref == "AUTO":
        decision_ref = f"decisions/{lease_id}/DEC-001.json"
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

    def test_disjoint_leases_can_coexist(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            a = base / "a.json"
            b = base / "b.json"
            self.write(a, lease("RUN-A", "example/a"))
            self.write(b, lease("RUN-B", "example/b"))
            acquire(a, locks, repo_root=ROOT)
            acquire(b, locks, repo_root=ROOT)
            self.assertEqual([], find_overlaps(lease("RUN-C", "example/c"), locks))

    def test_repository_overlap_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            a = base / "a.json"
            b = base / "b.json"
            self.write(a, lease("RUN-A", "example/product"))
            self.write(b, lease("RUN-B", "EXAMPLE/product"))
            acquire(a, locks, repo_root=ROOT)
            with self.assertRaises(RuntimeError):
                acquire(b, locks, repo_root=ROOT)

    def test_generation_guard_and_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            first = base / "first.json"
            second = base / "second.json"
            self.write(first, lease("RUN-A", "example/a"))
            acquire(first, locks, repo_root=ROOT)
            candidate = lease("RUN-A", "example/a", generation=2, decision_ref="decisions/RUN-A/DEC-002.json")
            candidate["repositories"].append({
                "repository": "example/b", "mode": "READ", "canonical_path": None,
                "worktree_root": None, "exact_sha": None,
            })
            self.write(second, candidate)
            replace(second, locks, expected_generation=1, repo_root=ROOT)
            released = release("RUN-A", locks, expected_generation=2, outcome_ref="runs/RUN-A/outcome.json")
            data = json.loads(released.read_text(encoding="utf-8"))
            self.assertEqual("RELEASED", data["state"])
            self.assertEqual(3, data["generation"])


    def test_coordination_repository_overlap_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            a = lease("RUN-A", "example/a")
            b = lease("RUN-B", "example/b")
            b["coordination_repository"] = a["coordination_repository"]
            pa, pb = base / "a.json", base / "b.json"
            self.write(pa, a)
            self.write(pb, b)
            acquire(pa, locks, repo_root=ROOT)
            with self.assertRaises(RuntimeError):
                acquire(pb, locks, repo_root=ROOT)

    def test_infrastructure_scope_overlap_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            a = lease("RUN-A", "example/a")
            b = lease("RUN-B", "example/b")
            a["infrastructure_scopes"] = ["host:lax"]
            b["infrastructure_scopes"] = ["HOST:LAX"]
            pa, pb = base / "a.json", base / "b.json"
            self.write(pa, a)
            self.write(pb, b)
            acquire(pa, locks, repo_root=ROOT)
            with self.assertRaises(RuntimeError):
                acquire(pb, locks, repo_root=ROOT)

    def test_stale_mutex_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            locks.mkdir()
            (locks / ".repo-set-admission.mutex").mkdir()
            candidate = base / "candidate.json"
            self.write(candidate, lease("RUN-A", "example/a"))
            with self.assertRaises(RuntimeError):
                acquire(candidate, locks, repo_root=ROOT)

    def test_replace_requires_decision_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            first = base / "first.json"
            second = base / "second.json"
            self.write(first, lease("RUN-A", "example/a"))
            acquire(first, locks, repo_root=ROOT)
            self.write(second, lease("RUN-A", "example/a", generation=2, decision_ref=None))
            with self.assertRaises(ValueError):
                replace(second, locks, expected_generation=1, repo_root=ROOT)


    def test_parent_child_path_overlap_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            a = lease("RUN-A", "example/a")
            b = lease("RUN-B", "example/b")
            a["local_scopes"] = ["V:/src/_worktrees/RUN-A"]
            b["local_scopes"] = ["v:/src/_worktrees/run-a/product"]
            pa, pb = base / "a.json", base / "b.json"
            self.write(pa, a)
            self.write(pb, b)
            acquire(pa, locks, repo_root=ROOT)
            with self.assertRaises(RuntimeError):
                acquire(pb, locks, repo_root=ROOT)

    def test_coordination_repository_conflicts_with_product_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            a = lease("RUN-A", "example/product")
            b = lease("RUN-B", "example/other")
            b["coordination_repository"] = "example/product"
            pa, pb = base / "a.json", base / "b.json"
            self.write(pa, a)
            self.write(pb, b)
            acquire(pa, locks, repo_root=ROOT)
            with self.assertRaises(RuntimeError):
                acquire(pb, locks, repo_root=ROOT)

    def test_active_writer_must_match_single_write_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            locks = base / "locks"
            candidate = lease("RUN-A", "example/a")
            candidate["repositories"].append({
                "repository": "example/b", "mode": "WRITE", "canonical_path": None,
                "worktree_root": None, "exact_sha": None,
            })
            path = base / "candidate.json"
            self.write(path, candidate)
            with self.assertRaises(ValueError):
                acquire(path, locks, repo_root=ROOT)


if __name__ == "__main__":
    unittest.main()

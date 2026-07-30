from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from coordination_loop_harness.leases import acquire, find_overlaps, release, replace
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

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from coordination_loop_harness.leases import find_overlaps
from coordination_loop_harness.validation import validate_document

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "compatibility" / "repo-set-lease.v2"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def lf_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


class RepositorySetLeaseV2ArtifactTests(unittest.TestCase):
    def test_shared_program_disjoint_writer_vector_has_no_overlap(self):
        vector = load(ARTIFACT_ROOT / "positive" / "shared-program-disjoint-writers.json")
        self.assertEqual([], validate_document(vector["left"], ROOT))
        self.assertEqual([], validate_document(vector["right"], ROOT))
        with tempfile.TemporaryDirectory() as tmp:
            locks = Path(tmp)
            (locks / "CLH-WRITER.lease.json").write_text(
                json.dumps(vector["left"]), encoding="utf-8"
            )
            overlaps = find_overlaps(vector["right"], locks)
        self.assertEqual(
            vector["expected_overlap_categories"], [item.category for item in overlaps]
        )

    def test_negative_vectors_cover_exact_resource_conflicts(self):
        vector = load(ARTIFACT_ROOT / "negative" / "overlap-cases.json")
        base = vector["base_lease"]
        self.assertEqual([], validate_document(base, ROOT))
        with tempfile.TemporaryDirectory() as tmp:
            locks = Path(tmp)
            (locks / "BASE-WRITER.lease.json").write_text(json.dumps(base), encoding="utf-8")
            for index, case in enumerate(vector["cases"], start=1):
                with self.subTest(case=case["case"]):
                    candidate = copy.deepcopy(base)
                    candidate["lease_id"] = f"CANDIDATE-{index}"
                    candidate["run_id"] = candidate["lease_id"]
                    candidate["owner"] = f"owner-{index}"
                    candidate["active_writer_repository"] = case["repository"]
                    candidate["local_scopes"] = case["local_scopes"]
                    candidate["infrastructure_scopes"] = case["infrastructure_scopes"]
                    candidate["repositories"][0].update(
                        {
                            "repository": case["repository"],
                            "canonical_path": f"V:/src/{case['repository'].split('/')[-1]}",
                            "worktree_root": case["worktree_root"],
                            "branch_ref": case["branch_ref"],
                            "exact_sha": str(index + 2) * 40,
                        }
                    )
                    self.assertEqual([], validate_document(candidate, ROOT))
                    categories = {item.category for item in find_overlaps(candidate, locks)}
                    self.assertIn(case["expected_category"], categories)

    def test_manifest_hashes_exact_artifact_set(self):
        manifest = load(ARTIFACT_ROOT / "artifact-manifest.json")
        records: list[bytes] = []
        for item in manifest["artifacts"]:
            path = ROOT / item["path"]
            digest = lf_digest(path)
            self.assertEqual(item["sha256"], digest, item["path"])
            records.append(item["path"].encode() + b"\0" + digest.encode() + b"\n")
        artifact_set_sha256 = hashlib.sha256(b"".join(records)).hexdigest()
        self.assertEqual(manifest["artifact_set_sha256"], artifact_set_sha256)
        self.assertEqual(
            manifest["producer_artifact_id"],
            f"repo-set-lease.v2+sha256:{artifact_set_sha256}",
        )


if __name__ == "__main__":
    unittest.main()

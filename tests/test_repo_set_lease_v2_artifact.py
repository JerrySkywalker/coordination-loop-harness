from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from coordination_loop_harness.decisions import verify_decision
from coordination_loop_harness.leases import (
    _validate_decision_scope,
    _validate_terminal_release,
    _validate_v2_lifecycle,
    find_overlaps,
    lease_candidate_sha256,
)
from coordination_loop_harness.util import canonical_json_bytes, load_json
from coordination_loop_harness.validation import validate_document

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT_ROOT = ROOT / "compatibility" / "repo-set-lease.v2"
EXPECTED_ARTIFACT_PATHS = [
    "compatibility/repo-set-lease.v2/negative/overlap-cases.json",
    "compatibility/repo-set-lease.v2/negative/schema-and-authority-cases.json",
    "compatibility/repo-set-lease.v2/positive/candidate-digest.json",
    "compatibility/repo-set-lease.v2/positive/shared-program-disjoint-writers.json",
    "compatibility/repo-set-lease.v2/positive/terminal-release.json",
    "docs/REPOSITORY_OWNERSHIP_V2.md",
    "docs/command-reference.md",
    "docs/leases.md",
    "schemas/decision.v2.schema.json",
    "schemas/repo-set-lease.v2.schema.json",
    "src/coordination_loop_harness/cli.py",
    "src/coordination_loop_harness/decisions.py",
    "src/coordination_loop_harness/leases.py",
    "src/coordination_loop_harness/repository.py",
    "src/coordination_loop_harness/util.py",
    "src/coordination_loop_harness/validation.py",
    "templates/decision.example.json",
    "tests/test_cli.py",
    "tests/test_leases.py",
    "tests/test_repo_set_lease_v2_artifact.py",
    "tests/test_repository.py",
    "tests/test_util.py",
]


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def lf_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def materialize_terminal_vector(
    root: Path,
    vector: dict,
    *,
    terminal: dict | None = None,
    active_decision: dict | None = None,
    release_decision: dict | None = None,
    extra_previous_ref: str | None = None,
) -> dict:
    terminal = copy.deepcopy(terminal or vector["terminal_candidate"])
    active_decision = copy.deepcopy(active_decision or vector["active_decision"])
    release_decision = copy.deepcopy(release_decision or vector["release_decision"])
    (root / "schemas").mkdir(parents=True)
    shutil.copy2(ROOT / "schemas" / "decision.v2.schema.json", root / "schemas")
    (root / "TEMPLATE_VERSION").write_text("test\n", encoding="utf-8")

    active_path = root / terminal["decision_ref"]
    active_path.parent.mkdir(parents=True, exist_ok=True)
    active_path.write_text(json.dumps(active_decision), encoding="utf-8")
    active_path.with_suffix(".md").write_text(
        vector["active_decision_markdown_utf8"], encoding="utf-8", newline=""
    )
    if extra_previous_ref is not None:
        previous_path = root / extra_previous_ref
        previous_path.parent.mkdir(parents=True, exist_ok=True)
        previous_path.write_text(json.dumps(active_decision), encoding="utf-8")
        previous_path.with_suffix(".md").write_text(
            vector["active_decision_markdown_utf8"], encoding="utf-8", newline=""
        )

    release_path = root / terminal["release_decision_ref"]
    release_path.parent.mkdir(parents=True, exist_ok=True)
    release_path.write_text(json.dumps(release_decision), encoding="utf-8")
    release_path.with_suffix(".md").write_text(
        vector["release_decision_markdown_utf8"], encoding="utf-8", newline=""
    )
    outcome_path = root / terminal["outcome_ref"]
    outcome_path.parent.mkdir(parents=True, exist_ok=True)
    outcome_path.write_text(vector["outcome_utf8"], encoding="utf-8", newline="")
    return terminal


class RepositorySetLeaseV2ArtifactTests(unittest.TestCase):
    def test_candidate_digest_vector_is_language_neutral_and_exact(self):
        vector = load(ARTIFACT_ROOT / "positive" / "candidate-digest.json")
        probe_bytes = canonical_json_bytes(vector["canonicalization_probe"])
        self.assertEqual(vector["canonicalization_probe_json_utf8"].encode(), probe_bytes)
        self.assertEqual(
            vector["canonicalization_probe_sha256"], hashlib.sha256(probe_bytes).hexdigest()
        )
        self.assertEqual([], validate_document(vector["candidate"], ROOT))
        self.assertEqual(
            vector["candidate_sha256"],
            lease_candidate_sha256(vector["candidate"]),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rejected.json"
            for raw in vector["rejected_json_utf8"]:
                with self.subTest(raw=raw):
                    path.write_text(raw, encoding="utf-8", newline="")
                    with self.assertRaises(ValueError):
                        load_json(path)

    def test_terminal_release_vector_binds_decision_and_outcome(self):
        vector = load(ARTIFACT_ROOT / "positive" / "terminal-release.json")
        terminal = vector["terminal_candidate"]
        decision = vector["release_decision"]
        self.assertEqual([], validate_document(terminal, ROOT))
        self.assertEqual([], validate_document(decision, ROOT))
        self.assertEqual(
            terminal["outcome_sha256"],
            hashlib.sha256(vector["outcome_utf8"].encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            decision["markdown_sha256"],
            hashlib.sha256(vector["release_decision_markdown_utf8"].encode("utf-8")).hexdigest(),
        )
        self.assertEqual(
            decision["lease_candidate_sha256"],
            lease_candidate_sha256(terminal),
        )
        self.assertEqual(terminal["decision_ref"], decision["previous_decision_ref"])
        self.assertEqual("lease:release", decision["authorized_actions"][0])
        self.assertEqual("NORMAL", terminal["release_authority"])
        active = vector["active_candidate"]
        active_decision = vector["active_decision"]
        self.assertEqual([], validate_document(active, ROOT))
        self.assertEqual([], validate_document(active_decision, ROOT))
        self.assertEqual(active_decision["lease_candidate_sha256"], lease_candidate_sha256(active))
        self.assertEqual(
            active_decision["markdown_sha256"],
            hashlib.sha256(vector["active_decision_markdown_utf8"].encode()).hexdigest(),
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            materialize_terminal_vector(root, vector)
            _validate_terminal_release(
                terminal,
                root,
                observed_now=datetime(2026, 9, 4, 0, 30, tzinfo=UTC),
            )

    def test_schema_enforces_portable_lifecycle_and_writer_counts(self):
        base = load(ARTIFACT_ROOT / "positive" / "shared-program-disjoint-writers.json")["left"]
        cases: list[tuple[str, dict]] = []

        active_with_terminal = copy.deepcopy(base)
        active_with_terminal["outcome_sha256"] = "a" * 64
        cases.append(("active-with-terminal-field", active_with_terminal))

        writer_missing = copy.deepcopy(base)
        writer_missing["repositories"][0]["mode"] = "READ"
        cases.append(("active-writer-without-write", writer_missing))

        writer_duplicated = copy.deepcopy(base)
        second = copy.deepcopy(writer_duplicated["repositories"][0])
        second["repository"] = "example/second"
        second["canonical_path"] = "V:/src/second"
        second["worktree_root"] = "V:/src/_worktrees/second"
        second["branch_ref"] = "refs/heads/agent/second"
        second["exact_sha"] = "2" * 40
        writer_duplicated["repositories"].append(second)
        cases.append(("two-writers", writer_duplicated))

        null_writer = copy.deepcopy(base)
        null_writer["active_writer_repository"] = None
        cases.append(("write-with-null-active-writer", null_writer))

        released_with_writer = copy.deepcopy(base)
        released_with_writer.update(
            {
                "state": "RELEASED",
                "generation": 2,
                "release_decision_ref": "decisions/CLH-WRITER/DEC-2.json",
                "release_authority": "NORMAL",
                "released_utc": "2026-09-04T00:30:00Z",
                "outcome_ref": "runs/CLH-WRITER/outcome.json",
                "outcome_sha256": "a" * 64,
            }
        )
        cases.append(("released-with-active-writer", released_with_writer))

        extra_property = copy.deepcopy(base)
        extra_property["unexpected"] = True
        cases.append(("extra-property", extra_property))

        for name, document in cases:
            with self.subTest(case=name):
                self.assertTrue(validate_document(document, ROOT))

    def test_negative_schema_and_authority_vectors_are_executable(self):
        vector = load(ARTIFACT_ROOT / "negative" / "schema-and-authority-cases.json")
        self.assertEqual(
            "compatibility/repo-set-lease.v2/positive/candidate-digest.json#/candidate",
            vector["bases"]["active"],
        )
        self.assertEqual(
            "compatibility/repo-set-lease.v2/positive/terminal-release.json#/terminal_candidate",
            vector["bases"]["terminal"],
        )
        bases = {
            "active": load(ARTIFACT_ROOT / "positive" / "candidate-digest.json")["candidate"],
            "terminal": load(ARTIFACT_ROOT / "positive" / "terminal-release.json")[
                "terminal_candidate"
            ],
        }
        for case in vector["document_cases"]:
            with self.subTest(case=case["case"]):
                document = copy.deepcopy(bases[case["base"]])
                for operation in case["patch"]:
                    tokens = operation["path"].lstrip("/").split("/")
                    target = document
                    for token in tokens[:-1]:
                        target = target[int(token)] if isinstance(target, list) else target[token]
                    final = tokens[-1]
                    if operation["op"] == "remove":
                        if isinstance(target, list):
                            del target[int(final)]
                        else:
                            del target[final]
                    elif operation["op"] == "add" and isinstance(target, list) and final == "-":
                        target.append(operation["value"])
                    else:
                        if isinstance(target, list):
                            target[int(final)] = operation["value"]
                        else:
                            target[final] = operation["value"]
                errors = validate_document(document, ROOT)
                if case["expected_layer"] == "SCHEMA_REJECT":
                    self.assertTrue(errors)
                else:
                    self.assertEqual([], errors)
                    with self.assertRaises(ValueError):
                        _validate_v2_lifecycle(document)

        terminal = bases["terminal"]
        authority = {item["case"]: item for item in vector["authority_cases"]}
        self.assertTrue(authority)
        self.assertTrue(
            all(case["expected"] == "AUTHORIZATION_REJECT" for case in authority.values())
        )
        terminal_vector = load(ARTIFACT_ROOT / "positive" / "terminal-release.json")
        release_decision = terminal_vector["release_decision"]
        mismatch = copy.deepcopy(release_decision)
        mismatch["lease_candidate_sha256"] = authority["candidate-digest-mismatch"][
            "supplied_candidate_sha256"
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            materialize_terminal_vector(root, terminal_vector, release_decision=mismatch)
            with self.assertRaisesRegex(ValueError, "candidate SHA-256 binding"):
                _validate_terminal_release(
                    terminal,
                    root,
                    observed_now=datetime(2026, 9, 4, 0, 30, tzinfo=UTC),
                )
        for case_name, digest_value in (
            ("lease-decision-missing-candidate-digest", ...),
            ("lease-decision-null-candidate-digest", None),
        ):
            with self.subTest(case=case_name):
                invalid = copy.deepcopy(release_decision)
                if digest_value is ...:
                    del invalid["lease_candidate_sha256"]
                else:
                    invalid["lease_candidate_sha256"] = digest_value
                # Generic decision.v2 remains structurally compatible with
                # historical lease decisions. V2 authority is the stronger
                # cross-document candidate binding below.
                self.assertEqual([], validate_document(invalid, ROOT))
                with self.assertRaisesRegex(ValueError, "candidate SHA-256 binding"):
                    _validate_decision_scope(terminal, invalid)
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    materialize_terminal_vector(
                        root,
                        terminal_vector,
                        release_decision=invalid,
                    )
                    result = verify_decision(
                        root,
                        root / terminal["release_decision_ref"],
                        run_id=terminal["run_id"],
                        action="lease:release",
                        lease_id=terminal["lease_id"],
                        lease_generation=terminal["generation"],
                        require_candidate_digest=True,
                    )
                    self.assertFalse(result["ok"])
                    self.assertIn(
                        "requires a non-null candidate SHA-256 binding",
                        "\n".join(result["findings"]),
                    )

        for case_name, field in (
            ("release-decision-wrong-lease-id", "lease_id"),
            ("release-decision-wrong-generation", "lease_generation"),
        ):
            with self.subTest(case=case_name):
                wrong_identity = copy.deepcopy(release_decision)
                wrong_identity[field] = authority[case_name][field]
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    materialize_terminal_vector(
                        root,
                        terminal_vector,
                        release_decision=wrong_identity,
                    )
                    with self.assertRaisesRegex(ValueError, "does not cover lease"):
                        _validate_terminal_release(
                            terminal,
                            root,
                            observed_now=datetime(2026, 9, 4, 0, 30, tzinfo=UTC),
                        )

        stale_terminal = copy.deepcopy(terminal)
        stale_terminal["release_authority"] = authority["normal-action-cannot-release-stale"][
            "release_authority"
        ]
        stale_decision = copy.deepcopy(release_decision)
        stale_decision["lease_candidate_sha256"] = lease_candidate_sha256(stale_terminal)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            materialize_terminal_vector(
                root,
                terminal_vector,
                terminal=stale_terminal,
                release_decision=stale_decision,
            )
            with self.assertRaisesRegex(ValueError, "lease:release-stale"):
                _validate_terminal_release(
                    stale_terminal,
                    root,
                    observed_now=datetime(2026, 9, 4, 2, 0, tzinfo=UTC),
                )

        mixed_decision = copy.deepcopy(release_decision)
        mixed_decision["authorized_actions"] = authority[
            "release-decision-cannot-mix-terminal-actions"
        ]["authorized_actions"]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            materialize_terminal_vector(root, terminal_vector, release_decision=mixed_decision)
            with self.assertRaisesRegex(ValueError, "exactly one terminal action"):
                _validate_terminal_release(
                    terminal,
                    root,
                    observed_now=datetime(2026, 9, 4, 0, 30, tzinfo=UTC),
                )

        predecessor = authority["release-must-directly-follow-active-decision"][
            "previous_decision_ref"
        ]
        wrong_predecessor = copy.deepcopy(release_decision)
        wrong_predecessor["previous_decision_ref"] = predecessor
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            materialize_terminal_vector(
                root,
                terminal_vector,
                release_decision=wrong_predecessor,
                extra_previous_ref=predecessor,
            )
            with self.assertRaisesRegex(ValueError, "does not directly follow"):
                _validate_terminal_release(
                    terminal,
                    root,
                    observed_now=datetime(2026, 9, 4, 0, 30, tzinfo=UTC),
                )

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
                    candidate["lease_id"] = case.get("lease_id", f"CANDIDATE-{index}")
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
                    categories = [item.category for item in find_overlaps(candidate, locks)]
                    self.assertEqual(case["expected_categories"], categories)

    def test_manifest_hashes_exact_artifact_set(self):
        manifest = load(ARTIFACT_ROOT / "artifact-manifest.json")
        paths = [item["path"] for item in manifest["artifacts"]]
        self.assertEqual(EXPECTED_ARTIFACT_PATHS, paths)
        self.assertEqual(len(paths), len(set(paths)))
        for path in paths:
            self.assertFalse(Path(path).is_absolute())
            self.assertNotIn("..", Path(path).parts)
            self.assertNotIn("\\", path)
        self.assertEqual(
            ["candidate-digest", "shared-program-disjoint-writers", "terminal-release"],
            manifest["positive_cases"],
        )
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

        overlap = load(ARTIFACT_ROOT / "negative" / "overlap-cases.json")
        self.assertEqual(
            {case["case"]: case["expected_categories"] for case in overlap["cases"]},
            manifest["negative_expected_categories"],
        )
        negative = load(ARTIFACT_ROOT / "negative" / "schema-and-authority-cases.json")
        expected_negative = [case["case"] for case in negative["document_cases"]]
        expected_negative.extend(case["case"] for case in negative["authority_cases"])
        self.assertEqual(
            expected_negative,
            manifest["negative_schema_and_authority_cases"],
        )


if __name__ == "__main__":
    unittest.main()

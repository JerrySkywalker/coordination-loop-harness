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
    _validate_lease,
    _validate_terminal_release,
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


def apply_operations(document: dict, operations: list[dict]) -> None:
    for operation in operations:
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
        elif isinstance(target, list):
            target[int(final)] = operation["value"]
        else:
            target[final] = operation["value"]


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
    shutil.copy2(ROOT / "schemas" / "repo-set-lease.v2.schema.json", root / "schemas")
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


def materialize_repository_root_files(root: Path, entries: list[dict]) -> None:
    for entry in entries:
        relative = entry["path"]
        path = Path(relative)
        if (
            not isinstance(relative, str)
            or not relative
            or path.is_absolute()
            or ".." in path.parts
            or "\\" in relative
        ):
            raise ValueError(f"invalid serialized repository-root path: {relative!r}")
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if set(entry) == {"path", "content_utf8"}:
            target.write_text(entry["content_utf8"], encoding="utf-8", newline="")
        elif set(entry) == {"path", "json_document"}:
            target.write_text(
                json.dumps(entry["json_document"], ensure_ascii=False) + "\n",
                encoding="utf-8",
                newline="",
            )
        elif set(entry) == {"path", "copy_artifact_bytes_from"}:
            source_relative = entry["copy_artifact_bytes_from"]
            source = Path(source_relative)
            if (
                not isinstance(source_relative, str)
                or not source_relative
                or source.is_absolute()
                or ".." in source.parts
                or "\\" in source_relative
            ):
                raise ValueError(f"invalid serialized artifact path: {source_relative!r}")
            shutil.copy2(ROOT / source, target)
        else:
            raise ValueError(f"unsupported serialized repository-root entry: {entry!r}")


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
        for value in vector["rejected_canonical_values"]:
            with self.subTest(canonical_value=value), self.assertRaises(ValueError):
                canonical_json_bytes(value)

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

    def test_terminal_overlap_vector_reserves_identity_and_releases_resources(self):
        vector = load(ARTIFACT_ROOT / "positive" / "terminal-release.json")
        self.assertEqual("repo-set-lease-v2-terminal-release.v2", vector["schema_version"])
        scenario = vector["overlap_scenario"]
        self.assertEqual(
            {
                "scenario",
                "operation",
                "repository_root",
                "lock_root_relative",
                "repository_root_files",
                "stored_entry",
                "probes",
            },
            set(scenario),
        )
        self.assertEqual("valid-terminal-overlap", scenario["scenario"])
        self.assertEqual("FIND_OVERLAPS", scenario["operation"])
        self.assertEqual("CREATE_EMPTY_DIRECTORY", scenario["repository_root"])
        self.assertEqual("locks", scenario["lock_root_relative"])
        files = {entry["path"]: entry for entry in scenario["repository_root_files"]}
        self.assertEqual(
            [
                "TEMPLATE_VERSION",
                "decisions/TERMINAL-VECTOR/DEC-1.json",
                "decisions/TERMINAL-VECTOR/DEC-1.md",
                "decisions/TERMINAL-VECTOR/DEC-2.json",
                "decisions/TERMINAL-VECTOR/DEC-2.md",
                "runs/TERMINAL-VECTOR/outcome.json",
                "schemas/decision.v2.schema.json",
                "schemas/repo-set-lease.v2.schema.json",
            ],
            list(files),
        )
        self.assertEqual(
            vector["active_decision"],
            files["decisions/TERMINAL-VECTOR/DEC-1.json"]["json_document"],
        )
        self.assertEqual(
            vector["active_decision_markdown_utf8"],
            files["decisions/TERMINAL-VECTOR/DEC-1.md"]["content_utf8"],
        )
        self.assertEqual(
            vector["release_decision"],
            files["decisions/TERMINAL-VECTOR/DEC-2.json"]["json_document"],
        )
        self.assertEqual(
            vector["release_decision_markdown_utf8"],
            files["decisions/TERMINAL-VECTOR/DEC-2.md"]["content_utf8"],
        )
        self.assertEqual(
            vector["outcome_utf8"],
            files["runs/TERMINAL-VECTOR/outcome.json"]["content_utf8"],
        )
        for schema_path in (
            "schemas/decision.v2.schema.json",
            "schemas/repo-set-lease.v2.schema.json",
        ):
            self.assertEqual(schema_path, files[schema_path]["copy_artifact_bytes_from"])
        stored = scenario["stored_entry"]
        self.assertEqual({"filename", "validation", "document"}, set(stored))
        self.assertEqual("VALID_TERMINAL", stored["validation"])
        self.assertEqual("TERMINAL-VECTOR.lease.json", stored["filename"])
        self.assertEqual(vector["terminal_candidate"], stored["document"])
        self.assertEqual(
            {"case", "candidate_validation", "candidate_document", "expected_overlaps"},
            set(scenario["probes"][0]),
        )
        self.assertEqual(
            {"case", "candidate_validation", "candidate_document", "expected_overlaps"},
            set(scenario["probes"][1]),
        )
        self.assertEqual(
            ["valid-terminal-casefold-id-refusal", "valid-terminal-resource-release"],
            [probe["case"] for probe in scenario["probes"]],
        )
        probes = {probe["case"]: probe for probe in scenario["probes"]}
        casefold_candidate = probes["valid-terminal-casefold-id-refusal"]["candidate_document"]
        self.assertEqual("terminal-vector", casefold_candidate["lease_id"])
        self.assertNotEqual(
            stored["document"]["repositories"][0]["repository"],
            casefold_candidate["repositories"][0]["repository"],
        )
        reuse_candidate = probes["valid-terminal-resource-release"]["candidate_document"]
        self.assertNotEqual(
            stored["document"]["lease_id"].casefold(),
            reuse_candidate["lease_id"].casefold(),
        )
        for field in ("repository", "canonical_path", "worktree_root", "branch_ref"):
            self.assertEqual(
                stored["document"]["repositories"][0][field],
                reuse_candidate["repositories"][0][field],
            )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            materialize_repository_root_files(root, scenario["repository_root_files"])
            terminal = stored["document"]
            _validate_terminal_release(
                terminal,
                root,
                observed_now=datetime(2026, 9, 4, 0, 30, tzinfo=UTC),
            )
            locks = root / scenario["lock_root_relative"]
            locks.mkdir()
            (locks / stored["filename"]).write_text(
                json.dumps(stored["document"], ensure_ascii=False) + "\n",
                encoding="utf-8",
                newline="",
            )
            for probe in scenario["probes"]:
                with self.subTest(case=probe["case"]):
                    self.assertEqual("SCHEMA_ACCEPT", probe["candidate_validation"])
                    self.assertEqual([], validate_document(probe["candidate_document"], ROOT))
                    overlaps = find_overlaps(
                        probe["candidate_document"],
                        locks,
                        repo_root=root,
                    )
                    serialized = [
                        {
                            "lease_id": overlap.lease_id,
                            "category": overlap.category,
                            "value": overlap.value,
                        }
                        for overlap in overlaps
                    ]
                    self.assertEqual(probe["expected_overlaps"], serialized)

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
        self.assertEqual(
            "compatibility/repo-set-lease.v2/positive/terminal-release.json#/release_decision",
            vector["bases"]["release_decision"],
        )
        self.assertEqual(
            "compatibility/repo-set-lease.v2/positive/terminal-release.json#/active_decision",
            vector["bases"]["active_decision"],
        )
        terminal_vector = load(ARTIFACT_ROOT / "positive" / "terminal-release.json")
        bases = {
            "active": load(ARTIFACT_ROOT / "positive" / "candidate-digest.json")["candidate"],
            "terminal": terminal_vector["terminal_candidate"],
            "release_decision": terminal_vector["release_decision"],
            "active_decision": terminal_vector["active_decision"],
        }
        for case in vector["document_cases"]:
            with self.subTest(case=case["case"]):
                document = copy.deepcopy(bases[case["base"]])
                apply_operations(document, case["patch"])
                errors = validate_document(document, ROOT)
                if case["expected_layer"] == "SCHEMA_REJECT":
                    self.assertTrue(errors)
                else:
                    self.assertEqual([], errors)
                    with self.assertRaises(ValueError):
                        _validate_lease(document, ROOT)

        for case in vector["decision_cases"]:
            with self.subTest(case=case["case"]):
                decision = copy.deepcopy(bases[case["base"]])
                apply_operations(decision, case["patch"])
                errors = validate_document(decision, ROOT)
                if case["expected_layer"] == "SCHEMA_REJECT":
                    self.assertTrue(errors)
                    continue
                self.assertEqual([], errors)
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    if case.get("target") == "PREDECESSOR":
                        materialize_terminal_vector(root, terminal_vector)
                        predecessor_path = root / bases["terminal"]["decision_ref"]
                        predecessor_path.write_text(json.dumps(decision), encoding="utf-8")
                    else:
                        materialize_terminal_vector(
                            root,
                            terminal_vector,
                            release_decision=decision,
                        )
                    result = verify_decision(
                        root,
                        root / bases["terminal"]["release_decision_ref"],
                        run_id=bases["terminal"]["run_id"],
                        action="lease:release",
                        lease_id=bases["terminal"]["lease_id"],
                        lease_generation=bases["terminal"]["generation"],
                        require_candidate_digest=True,
                    )
                    self.assertFalse(result["ok"])

        terminal = bases["terminal"]
        authority = {item["case"]: item for item in vector["authority_cases"]}
        self.assertTrue(authority)
        self.assertTrue(
            all(case["expected"] == "AUTHORIZATION_REJECT" for case in authority.values())
        )
        release_decision = terminal_vector["release_decision"]
        for case in vector["positive_authority_cases"]:
            with self.subTest(case=case["case"]):
                candidate = copy.deepcopy(terminal)
                accepted = copy.deepcopy(release_decision)
                if "candidate_infrastructure_scope" in case:
                    candidate["infrastructure_scopes"].append(
                        case["candidate_infrastructure_scope"]
                    )
                accepted["scope"].append(case["extra_scope"])
                accepted["lease_candidate_sha256"] = lease_candidate_sha256(candidate)
                _validate_decision_scope(candidate, accepted)
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

        for case_name in (
            "lease-decision-blank-scope-entry",
            "lease-decision-duplicate-scope-entry",
            "lease-decision-noncanonical-scope-entry",
        ):
            with self.subTest(case=case_name):
                invalid = copy.deepcopy(release_decision)
                case = authority[case_name]
                if "replace_scope_entry" in case:
                    index = invalid["scope"].index(case["replace_scope_entry"])
                    invalid["scope"][index] = case["scope_entry"]
                else:
                    invalid["scope"].append(case["scope_entry"])
                with self.assertRaisesRegex(ValueError, "canonical|unique"):
                    _validate_decision_scope(terminal, invalid)

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
        for side in ("left", "right"):
            self.assertEqual(
                ["example/shared-observer"],
                [
                    item["repository"]
                    for item in vector[side]["repositories"]
                    if item["mode"] == "READ"
                ],
            )
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
        self.assertEqual(
            {"schema_version", "operation", "scenarios"},
            set(vector),
        )
        self.assertEqual("repo-set-lease-v2-overlap-cases.v2", vector["schema_version"])
        self.assertEqual("FIND_OVERLAPS", vector["operation"])
        scenario_names: set[str] = set()
        case_names: set[str] = set()
        case_documents: dict[str, tuple[dict, dict]] = {}
        for scenario in vector["scenarios"]:
            self.assertEqual(
                {"scenario", "repository_root", "stored_entry", "probes"},
                set(scenario),
            )
            self.assertNotIn(scenario["scenario"], scenario_names)
            scenario_names.add(scenario["scenario"])
            self.assertEqual("OMIT", scenario["repository_root"])
            stored = scenario["stored_entry"]
            self.assertEqual({"filename", "validation", "document"}, set(stored))
            self.assertEqual(
                f"{stored['document']['lease_id']}.lease.json",
                stored["filename"],
            )
            stored_errors = validate_document(stored["document"], ROOT)
            if stored["validation"] == "SCHEMA_ACCEPT":
                self.assertEqual([], stored_errors)
            else:
                self.assertEqual("SCHEMA_REJECT", stored["validation"])
                self.assertTrue(stored_errors)
            for probe in scenario["probes"]:
                self.assertEqual(
                    {
                        "case",
                        "candidate_validation",
                        "candidate_document",
                        "expected_overlap_categories",
                    },
                    set(probe),
                )
                self.assertNotIn(probe["case"], case_names)
                case_names.add(probe["case"])
                candidate_errors = validate_document(probe["candidate_document"], ROOT)
                if probe["candidate_validation"] == "SCHEMA_ACCEPT":
                    self.assertEqual([], candidate_errors)
                else:
                    self.assertEqual("SCHEMA_REJECT", probe["candidate_validation"])
                    self.assertTrue(candidate_errors)
                with tempfile.TemporaryDirectory() as tmp:
                    locks = Path(tmp)
                    (locks / stored["filename"]).write_text(
                        json.dumps(stored["document"], ensure_ascii=False) + "\n",
                        encoding="utf-8",
                        newline="",
                    )
                    categories = [
                        item.category for item in find_overlaps(probe["candidate_document"], locks)
                    ]
                self.assertEqual(probe["expected_overlap_categories"], categories)
                case_documents[probe["case"]] = (
                    stored["document"],
                    probe["candidate_document"],
                )

        self.assertEqual(
            {
                "active-write-base",
                "active-read-base",
                "invalid-v2-relative-canonical-path",
                "invalid-v2-relative-worktree-root",
                "invalid-v2-relative-local-scope",
            },
            scenario_names,
        )
        left_reader, right_reader = case_documents["reader-versus-reader"]
        self.assertEqual("READ", left_reader["repositories"][0]["mode"])
        self.assertIsNone(left_reader["active_writer_repository"])
        self.assertEqual("READ", right_reader["repositories"][0]["mode"])
        self.assertIsNone(right_reader["active_writer_repository"])
        writer, reader = case_documents["writer-versus-reader"]
        self.assertEqual("WRITE", writer["repositories"][0]["mode"])
        self.assertEqual("example/clh", writer["active_writer_repository"])
        self.assertEqual("READ", reader["repositories"][0]["mode"])
        self.assertIsNone(reader["active_writer_repository"])
        reader, writer = case_documents["reader-versus-writer"]
        self.assertEqual("READ", reader["repositories"][0]["mode"])
        self.assertIsNone(reader["active_writer_repository"])
        self.assertEqual("WRITE", writer["repositories"][0]["mode"])
        self.assertEqual("example/clh", writer["active_writer_repository"])

        for case_name, field in (
            ("invalid-v2-relative-canonical-path", "canonical_path"),
            ("invalid-v2-relative-worktree-root", "worktree_root"),
        ):
            stored, candidate = case_documents[case_name]
            stored_binding = stored["repositories"][0]
            candidate_binding = candidate["repositories"][0]
            self.assertEqual("example/a", stored_binding["repository"])
            self.assertEqual(stored_binding["repository"], candidate_binding["repository"])
            self.assertEqual(".", stored_binding[field])
            self.assertEqual(".", candidate_binding[field])
            self.assertNotEqual(stored_binding["branch_ref"], candidate_binding["branch_ref"])

        stored, candidate = case_documents["invalid-v2-relative-local-scope"]
        self.assertEqual(["."], stored["local_scopes"])
        self.assertEqual(["."], candidate["local_scopes"])
        self.assertEqual(
            stored["repositories"][0]["repository"],
            candidate["repositories"][0]["repository"],
        )
        self.assertNotEqual(
            stored["repositories"][0]["branch_ref"],
            candidate["repositories"][0]["branch_ref"],
        )

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
        self.assertEqual(
            [
                "reader-versus-reader",
                "writer-versus-reader",
                "reader-versus-writer",
            ],
            manifest["access_mode_cases"],
        )
        self.assertEqual(
            "cooperating-admission-reserves-ascii-casefold-collisions-even-after-valid-terminal-release",
            manifest["lease_id_filename_policy"],
        )
        self.assertEqual(
            "relative-path-claims-have-no-cwd-derived-overlap-identity",
            manifest["invalid_v2_scope_policy"],
        )
        self.assertEqual(
            "repo-set-lease-v2-overlap-cases.v2",
            manifest["overlap_vector_schema"],
        )
        self.assertEqual(
            "repo-set-lease-v2-terminal-release.v2",
            manifest["terminal_release_vector_schema"],
        )
        self.assertEqual(
            "complete-stored-scenarios-complete-candidate-probes-and-explicit-repository-root-files-no-patches-defaults-indexes-document-pointers-or-generated-values",
            manifest["overlap_vector_encoding"],
        )
        self.assertEqual(
            "explicit-relative-path-with-exact-utf8-content-json-document-or-artifact-byte-copy",
            manifest["terminal_fixture_encoding"],
        )
        self.assertEqual(
            ["case-only-lease-id", "valid-terminal-casefold-id-refusal"],
            manifest["lease_id_filename_cases"],
        )
        self.assertEqual(
            ["reader-versus-reader", "valid-terminal-resource-release"],
            manifest["zero_overlap_cases"],
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
        overlap_probes = [
            probe for scenario in overlap["scenarios"] for probe in scenario["probes"]
        ]
        self.assertEqual(
            {probe["case"]: probe["expected_overlap_categories"] for probe in overlap_probes},
            manifest["negative_expected_categories"],
        )
        self.assertEqual(
            [
                "invalid-v2-relative-canonical-path",
                "invalid-v2-relative-worktree-root",
                "invalid-v2-relative-local-scope",
            ],
            manifest["invalid_v2_relative_overlap_cases"],
        )
        terminal = load(ARTIFACT_ROOT / "positive" / "terminal-release.json")
        self.assertEqual(
            {
                probe["case"]: [item["category"] for item in probe["expected_overlaps"]]
                for probe in terminal["overlap_scenario"]["probes"]
            },
            manifest["terminal_overlap_expected_categories"],
        )
        negative = load(ARTIFACT_ROOT / "negative" / "schema-and-authority-cases.json")
        expected_negative = [case["case"] for case in negative["document_cases"]]
        expected_negative.extend(case["case"] for case in negative["decision_cases"])
        expected_negative.extend(case["case"] for case in negative["authority_cases"])
        self.assertEqual(
            expected_negative,
            manifest["negative_schema_and_authority_cases"],
        )
        self.assertEqual(
            [case["case"] for case in negative["positive_authority_cases"]],
            manifest["positive_authority_cases"],
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from coordination_loop_harness.audit import SECRET_PATTERNS, record_audit, verify_audit
from coordination_loop_harness.binding import bind_goal
from coordination_loop_harness.blockers import evaluate_blocker
from coordination_loop_harness.bundles import seal_bundle, verify_bundle
from coordination_loop_harness.decisions import verify_decision
from coordination_loop_harness.runs import init_run
from coordination_loop_harness.status import transition_status
from coordination_loop_harness.util import sha256_file

ROOT = Path(__file__).resolve().parents[1]
SHA = "1" * 40


class Wave7ParityTests(unittest.TestCase):
    def test_static_fixture_is_synthetic_and_secret_free(self):
        fixture_root = ROOT / "tests" / "fixtures" / "wave7-style"
        fixture = json.loads((fixture_root / "fixture.json").read_text(encoding="utf-8"))
        self.assertEqual("example/synthetic-product", fixture["repository"])
        self.assertFalse(fixture["production_apply"])
        text = "\n".join(
            path.read_text(encoding="utf-8") for path in fixture_root.iterdir() if path.is_file()
        )
        self.assertTrue(all(not pattern.search(text) for pattern in SECRET_PATTERNS.values()))

    def run_root(self, base: Path) -> Path:
        root = base / "coordination"
        shutil.copytree(ROOT / "schemas", root / "schemas")
        shutil.copy(ROOT / "TEMPLATE_VERSION", root / "TEMPLATE_VERSION")
        init_run(
            root,
            run_id="W7-SYNTH-001",
            title="Synthetic Wave7 parity",
            requested_by="fixture-owner",
            objective="Prove secret-free coordination behavior.",
            repositories=["example/synthetic-product"],
            template_version="0.2.0",
        )
        return root

    def decision(
        self,
        root: Path,
        *,
        decision_id: str = "DEC-001",
        action: str = "status:admit",
    ) -> Path:
        directory = root / "decisions" / "W7-SYNTH-001"
        directory.mkdir(parents=True, exist_ok=True)
        markdown = directory / f"{decision_id}.md"
        markdown.write_text(f"# {decision_id}\n\nSynthetic authorization.\n", encoding="utf-8")
        document = {
            "schema_version": "coord.decision.v2",
            "decision_id": decision_id,
            "run_id": "W7-SYNTH-001",
            "sequence": 1,
            "decision_type": "OWNER_GATE",
            "status": "ACCEPTED",
            "issued_by": "fixture-owner",
            "issued_utc": "2026-01-01T00:00:00Z",
            "decision": "Authorize the synthetic action.",
            "rationale": "Fixture proof.",
            "scope": ["example/synthetic-product"],
            "conditions": ["No external mutation"],
            "authorized_actions": [action],
            "lease_id": None,
            "lease_generation": None,
            "previous_decision_ref": None,
            "markdown_sha256": sha256_file(markdown),
        }
        output = markdown.with_suffix(".json")
        output.write_text(json.dumps(document), encoding="utf-8")
        return output

    def chained_decision(
        self,
        root: Path,
        *,
        decision_id: str,
        sequence: int,
        previous_decision_ref: str | None,
        action: str = "status:admit",
    ) -> Path:
        directory = root / "decisions" / "W7-SYNTH-001"
        directory.mkdir(parents=True, exist_ok=True)
        markdown = directory / f"{decision_id}.md"
        markdown.write_text(f"# {decision_id}\n\nSynthetic authorization.\n", encoding="utf-8")
        document = {
            "schema_version": "coord.decision.v2",
            "decision_id": decision_id,
            "run_id": "W7-SYNTH-001",
            "sequence": sequence,
            "decision_type": "OWNER_GATE",
            "status": "ACCEPTED",
            "issued_by": "fixture-owner",
            "issued_utc": "2026-01-01T00:00:00Z",
            "decision": "Authorize the synthetic action.",
            "rationale": "Fixture proof.",
            "scope": ["example/synthetic-product"],
            "conditions": ["No external mutation"],
            "authorized_actions": [action],
            "lease_id": None,
            "lease_generation": None,
            "previous_decision_ref": previous_decision_ref,
            "markdown_sha256": sha256_file(markdown),
        }
        output = markdown.with_suffix(".json")
        output.write_text(json.dumps(document), encoding="utf-8")
        return output

    def directory_link(self, link: Path, target: Path) -> None:
        if os.name == "nt":
            result = subprocess.run(
                ["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                self.skipTest(f"Windows junction creation unavailable: {result.stderr}")
        else:
            link.symlink_to(target, target_is_directory=True)

    def remove_directory_link(self, link: Path) -> None:
        if not link.exists() and not link.is_symlink():
            return
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()

    def git_repository(self, base: Path) -> tuple[Path, str]:
        repo = base / "synthetic-product"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.name", "Fixture"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"],
            check=True,
        )
        (repo / "README.md").write_text("# Synthetic\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", "fixture"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "remote",
                "add",
                "origin",
                "https://github.com/example/synthetic-product.git",
            ],
            check=True,
        )
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return repo, head

    def test_bundle_seal_rejects_change_and_extra(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.run_root(Path(tmp))
            seal_bundle(root, "W7-SYNTH-001")
            self.assertTrue(verify_bundle(root, "W7-SYNTH-001")["ok"])
            request = root / "requests" / "W7-SYNTH-001" / "request.md"
            request.write_text(request.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")
            self.assertIn(
                "changed durable object",
                "\n".join(verify_bundle(root, "W7-SYNTH-001")["findings"]),
            )
            seal_bundle(root, "W7-SYNTH-001")
            (root / "audits" / "W7-SYNTH-001" / "extra.md").write_text(
                "# Extra\n", encoding="utf-8"
            )
            self.assertIn(
                "extra or unhashed",
                "\n".join(verify_bundle(root, "W7-SYNTH-001")["findings"]),
            )
            seal_bundle(root, "W7-SYNTH-001")
            request.unlink()
            self.assertIn(
                "missing durable object",
                "\n".join(verify_bundle(root, "W7-SYNTH-001")["findings"]),
            )

    def test_bundle_seal_rejects_reparse_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self.run_root(base)
            external = base / "external"
            external.mkdir()
            (external / "external.txt").write_text("outside\n", encoding="utf-8")
            link = root / "audits" / "W7-SYNTH-001" / "junction"
            link.parent.mkdir(parents=True, exist_ok=True)
            self.directory_link(link, external)
            try:
                with self.assertRaisesRegex(ValueError, "reparse point|symbolic link"):
                    seal_bundle(root, "W7-SYNTH-001")
            finally:
                self.remove_directory_link(link)

    def test_bundle_verify_rejects_reparse_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self.run_root(base)
            seal_path = seal_bundle(root, "W7-SYNTH-001")
            external = base / "external"
            external.mkdir()
            external_file = external / "external.txt"
            external_file.write_text("outside\n", encoding="utf-8")
            link = root / "audits" / "W7-SYNTH-001" / "junction"
            link.parent.mkdir(parents=True, exist_ok=True)
            self.directory_link(link, external)
            try:
                seal = json.loads(seal_path.read_text(encoding="utf-8"))
                seal["files"].append(
                    {
                        "path": "audits/W7-SYNTH-001/junction/external.txt",
                        "sha256": sha256_file(external_file),
                    }
                )
                seal_path.write_text(json.dumps(seal), encoding="utf-8")
                verified = verify_bundle(root, "W7-SYNTH-001")
                self.assertFalse(verified["ok"])
                self.assertIn("reparse point", "\n".join(verified["findings"]))
            finally:
                self.remove_directory_link(link)

    def test_bundle_seal_rejects_invalid_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.run_root(Path(tmp))
            request = root / "requests" / "W7-SYNTH-001" / "request.md"
            request.write_bytes(b"# Invalid UTF-8\n\xff\n")
            with self.assertRaisesRegex(ValueError, "valid UTF-8"):
                seal_bundle(root, "W7-SYNTH-001")

    def test_decision_verification_rejects_broken_transitive_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.run_root(Path(tmp))
            second = self.chained_decision(
                root,
                decision_id="DEC-002",
                sequence=2,
                previous_decision_ref=None,
            )
            third = self.chained_decision(
                root,
                decision_id="DEC-003",
                sequence=3,
                previous_decision_ref=second.relative_to(root).as_posix(),
            )
            verified = verify_decision(
                root,
                third,
                run_id="W7-SYNTH-001",
                action="status:admit",
            )
            self.assertFalse(verified["ok"])
            self.assertIn(
                "sequence greater than 1 requires previous_decision_ref",
                "\n".join(verified["findings"]),
            )

    def test_decision_status_blocker_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.run_root(Path(tmp))
            decision = self.decision(root)
            self.assertTrue(
                verify_decision(
                    root,
                    decision,
                    run_id="W7-SYNTH-001",
                    action="status:admit",
                )["ok"]
            )
            transition_status(
                root,
                "W7-SYNTH-001",
                target="ADMITTED",
                expected_generation=1,
                timestamp="2026-01-01T00:00:01Z",
                checkpoint="OWNER_GATE_ACCEPTED",
                decision_path=decision,
            )
            status = json.loads((root / "runs" / "W7-SYNTH-001" / "status.json").read_text())
            self.assertEqual("ADMITTED", status["state"])
            self.assertEqual(1, len(status["history"]))
            with self.assertRaises(ValueError):
                transition_status(
                    root,
                    "W7-SYNTH-001",
                    target="COMPLETE",
                    expected_generation=2,
                    timestamp="2026-01-01T00:00:02Z",
                    checkpoint="ILLEGAL",
                )

            first = evaluate_blocker(
                code="checkout-failed",
                summary="  Synthetic   failure ",
                scope="TEMP\\REPO",
                recurrence_count=1,
                retry_limit=1,
            )
            second = evaluate_blocker(
                code="CHECKOUT-FAILED",
                summary="Synthetic failure",
                scope="temp/repo",
                recurrence_count=2,
                retry_limit=1,
            )
            self.assertEqual(first["fingerprint"], second["fingerprint"])
            self.assertTrue(second["escalation_required"])

            audit_paths = record_audit(
                root,
                run_id="W7-SYNTH-001",
                audit_id="AUD-001",
                audit_type="EXACT_HEAD",
                auditor="fixture-auditor",
                timestamp="2026-01-01T00:00:03Z",
                audited_sha=SHA,
                result="PASS",
                findings=[],
                read_only_asserted=True,
                independently_launched_asserted=True,
            )
            verified = verify_audit(root, audit_paths[0])
            self.assertTrue(verified["ok"])
            self.assertIsNone(verified["verified"]["independently_launched"])

    def test_audit_verify_rejects_empty_non_pass_results(self):
        for result in ("FAIL", "BLOCKED"):
            with self.subTest(result=result), tempfile.TemporaryDirectory() as tmp:
                root = self.run_root(Path(tmp))
                audit_paths = record_audit(
                    root,
                    run_id="W7-SYNTH-001",
                    audit_id=f"AUD-{result}",
                    audit_type="EXACT_HEAD",
                    auditor="fixture-auditor",
                    timestamp="2026-01-01T00:00:03Z",
                    audited_sha=SHA,
                    result="PASS",
                    findings=[],
                    read_only_asserted=True,
                    independently_launched_asserted=True,
                )
                document = json.loads(audit_paths[0].read_text(encoding="utf-8"))
                document["result"] = result
                audit_paths[0].write_text(json.dumps(document), encoding="utf-8")
                verified = verify_audit(root, audit_paths[0])
                self.assertFalse(verified["ok"])
                self.assertIn(
                    f"{result} audit requires",
                    "\n".join(verified["findings"]),
                )

    def test_audit_record_rejects_empty_non_pass_results(self):
        for result in ("FAIL", "BLOCKED"):
            with self.subTest(result=result), tempfile.TemporaryDirectory() as tmp:
                root = self.run_root(Path(tmp))
                with self.assertRaisesRegex(ValueError, f"{result} audit requires"):
                    record_audit(
                        root,
                        run_id="W7-SYNTH-001",
                        audit_id=f"AUD-{result}",
                        audit_type="EXACT_HEAD",
                        auditor="fixture-auditor",
                        timestamp="2026-01-01T00:00:03Z",
                        audited_sha=SHA,
                        result=result,
                        findings=[],
                        read_only_asserted=True,
                        independently_launched_asserted=True,
                    )

    def test_bound_goal_is_local_and_does_not_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = self.run_root(base)
            repository, head = self.git_repository(base)
            state_root = base / "local-state"
            outputs = bind_goal(
                root,
                "W7-SYNTH-001",
                repository_root=repository,
                state_root=state_root,
                expected_origin="example/synthetic-product",
                stable_branch="main",
                expected_input_sha=head,
            )
            self.assertEqual(
                {"bound-goal.md", "coordinator-manifest.json", "implementer-attach.md"},
                {path.name for path in outputs},
            )
            attach = (state_root / "W7-SYNTH-001" / "implementer-attach.md").read_text()
            self.assertIn("PROCESS_STARTED=false", attach)
            manifest = json.loads(
                (state_root / "W7-SYNTH-001" / "coordinator-manifest.json").read_text()
            )
            self.assertFalse(manifest["process_launch_allowed"])
            self.assertNotIn("token", json.dumps(manifest).casefold())

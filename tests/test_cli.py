from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from coordination_loop_harness import cli


class CliTests(unittest.TestCase):
    def test_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "coordination_loop_harness.cli", "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode)
        self.assertIn("Coordination Loop Harness", result.stdout)
        self.assertIn("Legacy local renderer", result.stdout)
        self.assertIn("CLT", result.stdout)

    def test_new_command_help(self):
        commands = [
            ["bundle", "seal", "--help"],
            ["bundle", "verify", "--help"],
            ["bind-goal", "--help"],
            ["repository", "verify", "--help"],
            ["decision", "verify", "--help"],
            ["status", "transition", "--help"],
            ["blocker", "evaluate", "--help"],
            ["audit", "record", "--help"],
            ["audit", "verify", "--help"],
            ["bootstrap-repository", "--help"],
            ["template", "sync-plan", "--help"],
            ["harness", "validate", "--help"],
            ["lease", "inspect", "--help"],
            ["lease", "observe", "--help"],
            ["lease", "release", "--help"],
        ]
        for arguments in commands:
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "coordination_loop_harness.cli",
                        *arguments,
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, result.returncode, result.stderr)

    def test_lease_commands_do_not_inherit_ambient_repo_root(self):
        parser = cli.build_parser()
        commands = (
            ["lease", "acquire", "--candidate", "candidate.json", "--lock-root", "locks"],
            [
                "lease",
                "replace",
                "--candidate",
                "candidate.json",
                "--lock-root",
                "locks",
                "--expected-generation",
                "1",
            ],
            ["lease", "inspect", "--candidate", "candidate.json", "--lock-root", "locks"],
            ["lease", "observe", "--lease-id", "LEASE-1", "--lock-root", "locks"],
            [
                "lease",
                "release",
                "--lease-id",
                "LEASE-1",
                "--lock-root",
                "locks",
                "--expected-generation",
                "1",
                "--outcome-ref",
                "outcome.json",
            ],
        )
        for arguments in commands:
            with self.subTest(arguments=arguments):
                self.assertIsNone(parser.parse_args(arguments).repo_root)

    def test_release_rejects_ambiguous_candidate_and_legacy_outcome(self):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "coordination_loop_harness.cli",
                "lease",
                "release",
                "--lease-id",
                "LEASE-1",
                "--lock-root",
                ".",
                "--expected-generation",
                "1",
                "--candidate",
                "candidate.json",
                "--outcome-ref",
                "outcome.json",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(2, result.returncode)
        self.assertIn("not allowed with argument", result.stderr)

    def test_legacy_v1_release_cli_remains_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_root = Path(tmp)
            lease_path = lock_root / "LEGACY-CLI.lease.json"
            lease_path.write_text(
                json.dumps(
                    {
                        "schema_version": "coord.repo-set-lease.v1",
                        "lease_id": "LEGACY-CLI",
                        "run_id": "LEGACY-CLI",
                        "state": "ACTIVE",
                        "generation": 1,
                        "active_writer_repository": "example/repo",
                    }
                ),
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "coordination_loop_harness.cli",
                    "lease",
                    "release",
                    "--lease-id",
                    "LEGACY-CLI",
                    "--lock-root",
                    str(lock_root),
                    "--expected-generation",
                    "1",
                    "--outcome-ref",
                    "runs/LEGACY-CLI/outcome.json",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            released = json.loads(lease_path.read_text(encoding="utf-8"))
            self.assertEqual("RELEASED", released["state"])
            self.assertEqual(2, released["generation"])
            self.assertIsNone(released["active_writer_repository"])
            self.assertEqual("runs/LEGACY-CLI/outcome.json", released["outcome_ref"])

    def test_v2_release_cli_routes_exact_terminal_candidate_and_repo_root(self):
        with mock.patch.object(cli, "release", return_value=Path("released.json")) as release:
            with redirect_stdout(io.StringIO()):
                result = cli.main(
                    [
                        "lease",
                        "release",
                        "--lease-id",
                        "LEASE-2",
                        "--lock-root",
                        "locks",
                        "--expected-generation",
                        "2",
                        "--candidate",
                        "terminal.json",
                        "--repo-root",
                        "repository",
                    ]
                )
        self.assertEqual(0, result)
        release.assert_called_once_with(
            "LEASE-2",
            Path("locks").resolve(),
            expected_generation=2,
            outcome_ref=None,
            candidate_path=Path("terminal.json").resolve(),
            repo_root=Path("repository").resolve(),
        )


if __name__ == "__main__":
    unittest.main()

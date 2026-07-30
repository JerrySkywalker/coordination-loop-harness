from __future__ import annotations

import subprocess
import sys
import unittest


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


if __name__ == "__main__":
    unittest.main()

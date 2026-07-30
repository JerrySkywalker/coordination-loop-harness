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


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from coordination_loop_harness.audit import validate_repository
from coordination_loop_harness.runs import init_run, render_attach

ROOT = Path(__file__).resolve().parents[1]


class RunTests(unittest.TestCase):
    def test_init_validate_and_render_attach(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "repo"
            shutil.copytree(ROOT / "schemas", target / "schemas")
            shutil.copy(ROOT / "TEMPLATE_VERSION", target / "TEMPLATE_VERSION")
            (target / "scripts").mkdir()
            shutil.copy(
                ROOT / "scripts" / "Prepare-ImplementerAttach.ps1",
                target / "scripts" / "Prepare-ImplementerAttach.ps1",
            )
            init_run(
                target,
                run_id="DEMO-001",
                title="Demo",
                requested_by="owner",
                objective="Demonstrate the durable loop.",
                repositories=["example/product"],
                template_version="0.1.0",
            )
            output = render_attach(target, "DEMO-001")
            self.assertTrue(output.exists())
            self.assertIn("process", output.read_text(encoding="utf-8").lower())
            self.assertEqual([], validate_repository(target))
            status = json.loads((target / "runs" / "DEMO-001" / "status.json").read_text())
            self.assertEqual("PLANNED", status["state"])


if __name__ == "__main__":
    unittest.main()

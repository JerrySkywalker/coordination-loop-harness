from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from coordination_loop_harness.bootstrap import bootstrap_repository, sync_plan

ROOT = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    def test_ownership_manifest_covers_all_classes(self):
        manifest = json.loads(
            (ROOT / "templates" / "ownership-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {
                "template-managed",
                "render-once",
                "derived-owned",
                "template-source-only",
            },
            {item["classification"] for item in manifest["files"]},
        )

    def bootstrap(self, target: Path, *, dry_run: bool = False):
        return bootstrap_repository(
            ROOT,
            target,
            project_name="Synthetic Project",
            project_slug="synthetic-project",
            template_repository="example/coordination-template",
            template_version="0.2.0",
            template_sha="2" * 40,
            dry_run=dry_run,
            safe_mode=None,
        )

    def test_bootstrap_is_idempotent_and_preserves_render_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "derived"
            target.mkdir()
            self.bootstrap(target)
            second = self.bootstrap(target)
            self.assertTrue(
                all(item["action"] in {"unchanged", "template-only"} for item in second["actions"])
            )
            readme = target / "README.md"
            readme.write_text("# Derived-owned content\n", encoding="utf-8")
            third = self.bootstrap(target)
            action = next(item for item in third["actions"] if item["path"] == "README.md")
            self.assertEqual("preserve", action["action"])
            self.assertEqual("# Derived-owned content\n", readme.read_text(encoding="utf-8"))

    def test_first_bootstrap_renders_copied_template_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "derived"
            target.mkdir()
            (target / "README.md").write_text("# Template README\n", encoding="utf-8")
            result = self.bootstrap(target)
            action = next(item for item in result["actions"] if item["path"] == "README.md")
            self.assertEqual("render", action["action"])
            self.assertIn("Synthetic Project", (target / "README.md").read_text())

    def test_active_run_fails_closed_and_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "derived"
            status = target / "runs" / "ACTIVE-001" / "status.json"
            status.parent.mkdir(parents=True)
            status.write_text(json.dumps({"state": "RUNNING"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                self.bootstrap(target)
            plan = bootstrap_repository(
                ROOT,
                target,
                project_name="Synthetic Project",
                project_slug="synthetic-project",
                template_repository="example/coordination-template",
                template_version="0.2.0",
                template_sha="2" * 40,
                dry_run=True,
                safe_mode="preserve-active",
            )
            self.assertTrue(plan["dry_run"])
            self.assertFalse((target / "README.md").exists())
            self.assertTrue(status.exists())

    def test_sync_plan_is_non_mutating(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "derived"
            target.mkdir()
            self.bootstrap(target)
            before = (target / "README.md").read_bytes()
            plan = sync_plan(
                ROOT,
                target,
                project_name="Synthetic Project",
                project_slug="synthetic-project",
                template_version="0.2.1",
                template_sha="3" * 40,
            )
            self.assertEqual("non-mutating", plan["mode"])
            self.assertFalse(plan["apply_performed"])
            self.assertEqual("0.2.1", plan["to_template_version"])
            self.assertEqual(before, (target / "README.md").read_bytes())

    def test_workflow_contract(self):
        workflow = (ROOT / ".github" / "workflows" / "bootstrap-derived-repository.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("pull-requests: write", workflow)
        self.assertIn("git switch -c", workflow)
        self.assertIn("gh pr create", workflow)
        self.assertIn("--draft", workflow)
        self.assertNotIn("gh repo create", workflow)
        self.assertNotIn("push origin main", workflow)
        self.assertNotIn("PERSONAL_ACCESS_TOKEN", workflow)

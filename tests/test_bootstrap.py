from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from coordination_loop_harness.bootstrap import bootstrap_repository, sync_plan

ROOT = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    def template_repository(self, base: Path) -> tuple[Path, str]:
        template = base / "template"
        template.mkdir()
        shutil.copytree(ROOT / "schemas", template / "schemas")
        shutil.copytree(ROOT / "templates", template / "templates")
        shutil.copy(ROOT / "TEMPLATE_VERSION", template / "TEMPLATE_VERSION")
        (template / "TEMPLATE_VERSION").write_text("0.2.0\n", encoding="utf-8")
        subprocess.run(
            ["git", "init", "-b", "main", str(template)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(template), "config", "core.autocrlf", "false"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(template), "config", "user.name", "Fixture"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(template), "config", "user.email", "fixture@example.invalid"],
            check=True,
        )
        subprocess.run(["git", "-C", str(template), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(template), "commit", "-m", "template fixture"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(template),
                "remote",
                "add",
                "origin",
                "https://github.com/example/coordination-template.git",
            ],
            check=True,
        )
        head = subprocess.run(
            ["git", "-C", str(template), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        return template, head

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

    def bootstrap(
        self,
        template: Path,
        target: Path,
        *,
        template_version: str = "0.2.0",
        template_sha: str,
        dry_run: bool = False,
    ):
        return bootstrap_repository(
            template,
            target,
            project_name="Synthetic Project",
            project_slug="synthetic-project",
            template_repository="example/coordination-template",
            template_version=template_version,
            template_sha=template_sha,
            dry_run=dry_run,
            safe_mode=None,
        )

    def test_bootstrap_is_idempotent_and_preserves_render_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            template, template_sha = self.template_repository(base)
            target = base / "derived"
            target.mkdir()
            self.bootstrap(template, target, template_sha=template_sha)
            second = self.bootstrap(template, target, template_sha=template_sha)
            self.assertTrue(
                all(item["action"] in {"unchanged", "template-only"} for item in second["actions"])
            )
            readme = target / "README.md"
            readme.write_text("# Derived-owned content\n", encoding="utf-8")
            third = self.bootstrap(template, target, template_sha=template_sha)
            action = next(item for item in third["actions"] if item["path"] == "README.md")
            self.assertEqual("preserve", action["action"])
            self.assertEqual("# Derived-owned content\n", readme.read_text(encoding="utf-8"))

    def test_first_bootstrap_renders_copied_template_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            template, template_sha = self.template_repository(base)
            target = base / "derived"
            target.mkdir()
            (target / "README.md").write_text("# Template README\n", encoding="utf-8")
            result = self.bootstrap(template, target, template_sha=template_sha)
            action = next(item for item in result["actions"] if item["path"] == "README.md")
            self.assertEqual("render", action["action"])
            self.assertIn("Synthetic Project", (target / "README.md").read_text())

    def test_active_run_fails_closed_and_dry_run_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            template, template_sha = self.template_repository(base)
            target = base / "derived"
            status = target / "runs" / "ACTIVE-001" / "status.json"
            status.parent.mkdir(parents=True)
            status.write_text(json.dumps({"state": "RUNNING"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                self.bootstrap(template, target, template_sha=template_sha)
            plan = bootstrap_repository(
                template,
                target,
                project_name="Synthetic Project",
                project_slug="synthetic-project",
                template_repository="example/coordination-template",
                template_version="0.2.0",
                template_sha=template_sha,
                dry_run=True,
                safe_mode="preserve-active",
            )
            self.assertTrue(plan["dry_run"])
            self.assertFalse((target / "README.md").exists())
            self.assertTrue(status.exists())

    def test_sync_plan_is_non_mutating(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            template, old_sha = self.template_repository(base)
            target = base / "derived"
            target.mkdir()
            self.bootstrap(template, target, template_sha=old_sha)
            before = (target / "README.md").read_bytes()
            (template / "TEMPLATE_VERSION").write_text("0.2.1\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(template), "add", "TEMPLATE_VERSION"], check=True)
            subprocess.run(
                ["git", "-C", str(template), "commit", "-m", "template 0.2.1"],
                check=True,
                capture_output=True,
            )
            new_sha = subprocess.run(
                ["git", "-C", str(template), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            plan = sync_plan(
                template,
                target,
                project_name="Synthetic Project",
                project_slug="synthetic-project",
                template_version="0.2.1",
                template_sha=new_sha,
            )
            self.assertEqual("non-mutating", plan["mode"])
            self.assertFalse(plan["apply_performed"])
            self.assertEqual("0.2.1", plan["to_template_version"])
            self.assertEqual(before, (target / "README.md").read_bytes())

    def test_sync_plan_classifies_untouched_managed_file_as_safe_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            template, old_sha = self.template_repository(base)
            target = base / "derived"
            target.mkdir()
            self.bootstrap(template, target, template_sha=old_sha)
            lock = target / ".coord-template-lock.json"
            before = lock.read_bytes()

            (template / "TEMPLATE_VERSION").write_text("0.2.1\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(template), "add", "TEMPLATE_VERSION"], check=True)
            subprocess.run(
                ["git", "-C", str(template), "commit", "-m", "template 0.2.1"],
                check=True,
                capture_output=True,
            )
            new_sha = subprocess.run(
                ["git", "-C", str(template), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            plan = sync_plan(
                template,
                target,
                project_name="Synthetic Project",
                project_slug="synthetic-project",
                template_version="0.2.1",
                template_sha=new_sha,
            )
            action = next(
                item for item in plan["actions"] if item["path"] == ".coord-template-lock.json"
            )
            self.assertEqual("safe-update", action["action"])
            self.assertFalse(plan["conflicts"])
            self.assertEqual(before, lock.read_bytes())

    def test_sync_plan_keeps_modified_managed_file_as_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            template, old_sha = self.template_repository(base)
            target = base / "derived"
            target.mkdir()
            self.bootstrap(template, target, template_sha=old_sha)
            lock = target / ".coord-template-lock.json"
            lock.write_text('{"derived_edit": true}\n', encoding="utf-8")

            (template / "TEMPLATE_VERSION").write_text("0.2.1\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(template), "add", "TEMPLATE_VERSION"], check=True)
            subprocess.run(
                ["git", "-C", str(template), "commit", "-m", "template 0.2.1"],
                check=True,
                capture_output=True,
            )
            new_sha = subprocess.run(
                ["git", "-C", str(template), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            plan = sync_plan(
                template,
                target,
                project_name="Synthetic Project",
                project_slug="synthetic-project",
                template_version="0.2.1",
                template_sha=new_sha,
            )
            action = next(
                item for item in plan["actions"] if item["path"] == ".coord-template-lock.json"
            )
            self.assertEqual("conflict", action["action"])
            self.assertEqual([action], plan["conflicts"])
            self.assertEqual('{"derived_edit": true}\n', lock.read_text(encoding="utf-8"))

    def test_bootstrap_rejects_unbound_template_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            template, _ = self.template_repository(base)
            target = base / "derived"
            target.mkdir()
            with self.assertRaisesRegex(ValueError, "template provenance"):
                self.bootstrap(template, target, template_sha="f" * 40)
            self.assertFalse((target / ".coord-template.json").exists())

    def test_bootstrap_json_escapes_project_name_before_any_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            template, template_sha = self.template_repository(base)
            target = base / "derived"
            target.mkdir()
            result = bootstrap_repository(
                template,
                target,
                project_name='Broken " Project',
                project_slug="synthetic-project",
                template_repository="example/coordination-template",
                template_version="0.2.0",
                template_sha=template_sha,
                dry_run=False,
                safe_mode=None,
            )
            self.assertTrue(result["ok"])
            rendered = json.loads((target / "coord-project.json").read_text(encoding="utf-8"))
            self.assertEqual('Broken " Project', rendered["project_name"])

    def test_bootstrap_binds_derived_checkout_to_template_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            template, template_sha = self.template_repository(base)
            template_tree = subprocess.run(
                ["git", "-C", str(template), "rev-parse", "HEAD^{tree}"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            derived = base / "derived"
            subprocess.run(
                ["git", "clone", "--no-hardlinks", str(template), str(derived)],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "-C", str(derived), "config", "user.name", "Fixture"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(derived), "config", "user.email", "fixture@example.invalid"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(derived), "commit", "--allow-empty", "-m", "derived history"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(derived),
                    "remote",
                    "set-url",
                    "origin",
                    "https://github.com/example/derived.git",
                ],
                check=True,
            )
            fake_gh = base / "fake-gh.py"
            fake_gh.write_text(f"print({template_tree!r})\n", encoding="utf-8")

            result = bootstrap_repository(
                derived,
                derived,
                project_name="Synthetic Project",
                project_slug="synthetic-project",
                template_repository="example/coordination-template",
                template_version="0.2.0",
                template_sha=template_sha,
                dry_run=False,
                safe_mode=None,
                target_repository="example/derived",
                gh_command=str(fake_gh),
            )
            self.assertEqual(
                "github-template-tree",
                result["provenance_verification"]["verification"],
            )
            provenance = json.loads((derived / ".coord-template.json").read_text(encoding="utf-8"))
            self.assertEqual(template_sha, provenance["template_exact_sha"])

    def test_v5_has_no_active_clh_bootstrap_distribution_workflow(self):
        workflow = ROOT / ".github" / "workflows" / "bootstrap-derived-repository.yml"
        self.assertFalse(workflow.exists())
        boundary = (ROOT / "docs" / "CLH_CLT_BOUNDARY.md").read_text(encoding="utf-8")
        self.assertIn("CLT is the sole active starter", boundary)
        self.assertIn("frozen v0.2/v0.3 local compatibility window", boundary)

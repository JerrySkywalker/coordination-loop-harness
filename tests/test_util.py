from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from coordination_loop_harness.util import (
    MAX_SAFE_JSON_INTEGER,
    MOVEFILE_REPLACE_EXISTING,
    MOVEFILE_WRITE_THROUGH,
    admission_mutex,
    canonical_json_bytes,
    canonical_repo,
    canonical_repo_v2,
    canonical_scope,
    ensure_within,
    is_native_absolute_scope,
    is_v2_absolute_scope,
    load_json,
    paths_overlap,
    windows_device_scope_alias,
    write_json_atomic,
)


class PathNormalizationTests(unittest.TestCase):
    def test_windows_and_unc_paths_are_casefolded(self):
        self.assertEqual("v:/src/example", canonical_scope(r"V:\SRC\Example"))
        self.assertEqual("//server/share/repo", canonical_scope(r"\\SERVER\Share\Repo"))

    def test_windows_dot_segments_and_separators_are_canonical(self):
        self.assertEqual(
            canonical_scope(r"V:\src\scope\target"),
            canonical_scope(r"v:/SRC/scope/child/../target/"),
        )
        self.assertTrue(
            paths_overlap(
                r"V:\src\scope\target",
                r"v:/SRC/scope/target/child",
            )
        )
        self.assertFalse(
            paths_overlap(
                r"V:\src\scope\target",
                r"V:\src\scope\target-sibling",
            )
        )

    def test_posix_path_keeps_platform_semantics(self):
        raw = "/tmp/example"
        expected = str(Path(raw).resolve()) if os.name == "posix" else raw
        self.assertEqual(expected, canonical_scope(raw))

    @unittest.skipUnless(os.name == "posix", "POSIX filesystem alias test")
    def test_posix_aliases_share_resolved_identity(self):
        raw = "/tmp/example"
        resolved = str(Path(raw).resolve())
        self.assertEqual(canonical_scope(raw), canonical_scope(resolved))
        self.assertTrue(paths_overlap(raw, resolved))

    def test_reparse_or_symlink_resolution_and_escape_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            trusted = base / "trusted"
            external = base / "external"
            trusted.mkdir()
            external.mkdir()
            link = trusted / "link"
            if os.name == "nt":
                created = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link), str(external)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if created.returncode != 0:
                    self.skipTest("Windows junction creation is unavailable")
            else:
                try:
                    link.symlink_to(external, target_is_directory=True)
                except OSError as exc:
                    self.skipTest(f"directory symlink creation is unavailable: {exc}")
            self.assertEqual(
                canonical_scope(str(external)),
                canonical_scope(str(link)),
            )
            with self.assertRaisesRegex(ValueError, "must stay within"):
                ensure_within(link / "victim.json", trusted)

    def test_common_github_origins_have_one_identity(self):
        expected = "example/repo"
        self.assertEqual(expected, canonical_repo("https://github.com/Example/Repo.git"))
        self.assertEqual(expected, canonical_repo("git@github.com:Example/Repo.git"))
        self.assertEqual(expected, canonical_repo("ssh://git@github.com/Example/Repo.git"))

    def test_v2_repository_aliases_are_defensive_without_changing_v1(self):
        self.assertEqual("example/repo.git", canonical_repo("Example/Repo.GIT"))
        self.assertEqual("example/repo", canonical_repo_v2("Example/Repo.GIT"))
        self.assertEqual(
            "example/repo",
            canonical_repo_v2("git@GITHUB.COM:Example/Repo.GIT"),
        )

    def test_v2_path_grammar_default_denies_ambiguous_namespaces(self):
        self.assertTrue(is_v2_absolute_scope("V:/src/repo"))
        self.assertTrue(is_v2_absolute_scope("/tmp/repo"))
        for value in (
            r"\\?\V:\src\repo",
            r"\\.\V:\src\repo",
            r"\\.\pipe\coordination",
            r"\\server\share\repo",
            "//server/share/repo",
            "//tmp",
            "///tmp",
            "/tmp/a\\b",
        ):
            with self.subTest(value=value):
                self.assertFalse(is_v2_absolute_scope(value))
        if os.name == "nt":
            self.assertTrue(is_native_absolute_scope("V:/src/repo"))
            self.assertFalse(is_native_absolute_scope("/src/repo"))
        else:
            self.assertTrue(is_native_absolute_scope("/tmp/repo"))
            self.assertFalse(is_native_absolute_scope("V:/src/repo"))

    def test_recognized_device_aliases_are_retained_for_conservative_scans(self):
        extended = windows_device_scope_alias(r"\\?\V:\src\repo")
        device = windows_device_scope_alias(r"\\.\V:\src\repo")
        self.assertIsNotNone(extended)
        self.assertIsNotNone(device)
        self.assertEqual(canonical_scope("V:/src/repo"), canonical_scope(extended or ""))
        self.assertEqual(canonical_scope("V:/src/repo"), canonical_scope(device or ""))
        self.assertIsNone(windows_device_scope_alias(r"\\.\pipe\coordination"))


class AtomicJsonTests(unittest.TestCase):
    def test_admission_mutex_refuses_replaced_directory_without_touching_external_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            locks = root / "locks"
            external = root / "external"
            stolen = root / "stolen-mutex"
            locks.mkdir()
            external.mkdir()
            external_owner = external / "owner.json"
            external_owner.write_bytes(b"external-owner\n")
            mutex = locks / ".repo-set-admission.mutex"
            try:
                with self.assertRaisesRegex(RuntimeError, "mutex identity changed"):
                    with admission_mutex(locks):
                        mutex.rename(stolen)
                        if os.name == "nt":
                            created = subprocess.run(
                                ["cmd", "/c", "mklink", "/J", str(mutex), str(external)],
                                check=False,
                                capture_output=True,
                                text=True,
                            )
                            if created.returncode != 0:
                                stolen.rename(mutex)
                                self.skipTest("Windows junction creation is unavailable")
                        else:
                            mutex.symlink_to(external, target_is_directory=True)
                self.assertEqual(b"external-owner\n", external_owner.read_bytes())
            finally:
                if os.name == "nt" and hasattr(os.path, "isjunction"):
                    if os.path.isjunction(mutex):
                        os.rmdir(mutex)
                elif mutex.is_symlink():
                    mutex.unlink()
                if stolen.exists():
                    stolen.rmdir()

    def test_admission_mutex_never_enumerates_or_deletes_unexpected_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            locks = Path(tmp) / "locks"
            locks.mkdir()
            mutex = locks / ".repo-set-admission.mutex"
            with self.assertRaises(OSError):
                with admission_mutex(locks):
                    (mutex / "foreign-record").write_bytes(b"preserve\n")
            self.assertEqual(b"preserve\n", (mutex / "foreign-record").read_bytes())

    def test_load_json_rejects_duplicate_object_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "duplicate.json"
            path.write_text('{"lease_id":"one","lease_id":"two"}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Duplicate JSON object key: lease_id"):
                load_json(path)

    def test_load_json_rejects_non_finite_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            for spelling in ("NaN", "Infinity", "-Infinity"):
                with self.subTest(spelling=spelling):
                    path = Path(tmp) / "non-finite.json"
                    path.write_text(f'{{"value":{spelling}}}\n', encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "Non-finite JSON number"):
                        load_json(path)

    def test_canonical_and_durable_json_reject_programmatic_non_finite_numbers(self):
        with self.assertRaises(ValueError):
            canonical_json_bytes({"value": float("nan")})
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "record.json"
            with self.assertRaises(ValueError):
                write_json_atomic(target, {"value": float("inf")})
            self.assertFalse(target.exists())

    def test_canonical_json_uses_only_portable_safe_integers(self):
        self.assertEqual(
            b'{"max_safe_integer":9007199254740991}\n',
            canonical_json_bytes({"max_safe_integer": MAX_SAFE_JSON_INTEGER}),
        )
        for value in (MAX_SAFE_JSON_INTEGER + 1, -(MAX_SAFE_JSON_INTEGER + 1), 1.0):
            with (
                self.subTest(value=value),
                self.assertRaisesRegex(ValueError, "safe integer|floating-point"),
            ):
                canonical_json_bytes({"value": value})

    def test_create_new_preserves_existing_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "record.json"
            target.write_bytes(b"original\n")
            with self.assertRaises(FileExistsError):
                write_json_atomic(target, {"replacement": True}, create_new=True)
            self.assertEqual(b"original\n", target.read_bytes())
            self.assertEqual([], list(root.glob("*.tmp")))

    def test_create_new_publish_failure_leaves_no_partial_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "record.json"
            primitive = (
                "coordination_loop_harness.util._move_file_windows"
                if os.name == "nt"
                else "coordination_loop_harness.util.os.link"
            )
            with mock.patch(
                primitive,
                side_effect=OSError("synthetic publish failure"),
            ):
                with self.assertRaisesRegex(OSError, "synthetic publish failure"):
                    write_json_atomic(target, {"complete": True}, create_new=True)
            self.assertFalse(target.exists())
            self.assertEqual([], list(root.glob("*.tmp")))

    @unittest.skipUnless(os.name == "nt", "Windows write-through publication test")
    def test_windows_move_flags_keep_create_new_no_replace_and_flush_both_paths(self):
        self.assertEqual(0x8, MOVEFILE_WRITE_THROUGH)
        self.assertEqual(0x1, MOVEFILE_REPLACE_EXISTING)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "record.json"
            write_json_atomic(target, {"generation": 1}, create_new=True)
            self.assertEqual({"generation": 1}, load_json(target))
            with self.assertRaises(FileExistsError):
                write_json_atomic(target, {"generation": 2}, create_new=True)
            write_json_atomic(target, {"generation": 2})
            self.assertEqual({"generation": 2}, load_json(target))

    def test_create_new_write_failure_closes_and_cleans_temporary_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "record.json"
            with mock.patch(
                "coordination_loop_harness.util.os.fsync",
                side_effect=OSError("synthetic fsync failure"),
            ):
                with self.assertRaisesRegex(OSError, "synthetic fsync failure"):
                    write_json_atomic(target, {"complete": True}, create_new=True)
            self.assertFalse(target.exists())
            self.assertEqual([], list(root.glob("*.tmp")))

    def test_directory_sync_failure_reports_ambiguous_published_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "record.json"
            with mock.patch(
                "coordination_loop_harness.util._fsync_directory",
                side_effect=OSError("synthetic directory sync failure"),
            ):
                with self.assertRaisesRegex(OSError, "synthetic directory sync failure"):
                    write_json_atomic(target, {"complete": True}, create_new=True)
            self.assertEqual({"complete": True}, json.loads(target.read_text(encoding="utf-8")))
            self.assertEqual([], list(root.glob("*.tmp")))

    @unittest.skipUnless(os.name == "posix", "POSIX open-reader rename semantics")
    def test_posix_replace_readers_observe_complete_old_or_new_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "record.json"
            old = {"generation": 1, "payload": "a" * 10000}
            new = {"generation": 2, "payload": "b" * 10000}
            write_json_atomic(target, old, create_new=True)
            old_bytes = target.read_bytes()
            new_bytes = (
                json.dumps(new, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
            ).encode("utf-8")

            with target.open("rb") as reader_opened_before_replace:
                write_json_atomic(target, new)
                self.assertEqual(old_bytes, reader_opened_before_replace.read())

            self.assertEqual(new_bytes, target.read_bytes())
            self.assertEqual([], list(root.glob("*.tmp")))

    @unittest.skipUnless(os.name == "posix", "POSIX directory durability semantics")
    def test_posix_replace_directory_sync_failure_reports_without_rollback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "record.json"
            new = {"generation": 2, "payload": "complete"}
            write_json_atomic(target, {"generation": 1}, create_new=True)
            new_bytes = (
                json.dumps(new, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
            ).encode("utf-8")
            real_replace = os.replace
            failure = OSError("synthetic directory sync failure")

            with (
                mock.patch(
                    "coordination_loop_harness.util.os.replace",
                    side_effect=real_replace,
                ) as replace_mock,
                mock.patch(
                    "coordination_loop_harness.util._fsync_directory",
                    side_effect=failure,
                ) as sync_mock,
                self.assertRaises(OSError) as raised,
            ):
                write_json_atomic(target, new)

            self.assertIs(failure, raised.exception)
            replace_mock.assert_called_once()
            sync_mock.assert_called_once_with(root)
            self.assertEqual(new_bytes, target.read_bytes())
            self.assertEqual([], list(root.glob("*.tmp")))

    def test_multiprocess_create_new_publishes_one_complete_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "record.json"
            barrier = root / "start"
            script = (
                "import sys,time; from pathlib import Path; "
                "from coordination_loop_harness.util import write_json_atomic; "
                "target,barrier=map(Path,sys.argv[1:3]); value=int(sys.argv[3]); "
                "\nwhile not barrier.exists(): time.sleep(0.01); "
                "\nwrite_json_atomic(target,{'writer':value,'payload':'x'*10000},create_new=True); "
                "print('PUBLISHED')"
            )
            processes = [
                subprocess.Popen(
                    [sys.executable, "-c", script, str(target), str(barrier), str(index)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                for index in range(4)
            ]
            barrier.write_text("go\n", encoding="utf-8")
            results = [process.communicate(timeout=20) for process in processes]
            self.assertEqual(1, sum(process.returncode == 0 for process in processes))
            self.assertEqual(1, sum("PUBLISHED" in stdout for stdout, _ in results))
            document = json.loads(target.read_text(encoding="utf-8"))
            self.assertIn(document["writer"], range(4))
            self.assertEqual("x" * 10000, document["payload"])
            self.assertEqual([], list(root.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()

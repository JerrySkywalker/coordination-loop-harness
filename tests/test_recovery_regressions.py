from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

import test_leases
from mcp import Client
from test_mcp_server import v2_candidates

from coordination_loop_harness import leases, mcp_server, util


class RecoveryRegressionTests(unittest.TestCase):
    def test_utc_sample_preserves_fractional_precision(self):
        with mock.patch.object(util, "datetime") as clock:
            clock.now.return_value = datetime(2099, 1, 1, microsecond=200000, tzinfo=UTC)
            self.assertEqual("2099-01-01T00:00:00.200000Z", util.utc_now())

    def _release_fixture(self, root):
        fixture = test_leases.LeaseTests()
        repository = fixture.repository_worktree(root, "product", repository="example/product")
        active = fixture.lease_v2("FRACTION", "example/product", *repository)
        active["expires_utc"] = "2099-01-01T00:00:00.100Z"
        fixture.authorize(root, active, "lease:acquire")
        candidate = root / "active.json"
        fixture.write(candidate, active)
        locks = root / "locks"
        leases.acquire(candidate, locks, repo_root=root)
        _, terminal = fixture.terminal_v2(
            root, active, authority="NORMAL", released_utc="2026-09-04T00:30:00Z"
        )
        return locks, terminal

    def test_fractional_expiry_cannot_use_normal_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            locks, terminal = self._release_fixture(root)
            with (
                mock.patch.object(leases, "utc_now", return_value="2099-01-01T00:00:00.200Z"),
                self.assertRaisesRegex(ValueError, "STALE_RECOVERY"),
            ):
                leases.release(
                    "FRACTION",
                    locks,
                    expected_generation=1,
                    candidate_path=terminal,
                    repo_root=root,
                )
            self.assertEqual(
                "ACTIVE", json.loads((locks / "FRACTION.lease.json").read_text())["state"]
            )

    def test_release_rechecks_expiry_after_temporary_file_fsync(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            locks, terminal = self._release_fixture(root)
            samples = ["2099-01-01T00:00:00.000Z"] * 3 + ["2099-01-01T00:00:00.200Z"]
            with (
                mock.patch.object(leases, "utc_now", side_effect=samples) as clock,
                self.assertRaisesRegex(ValueError, "STALE_RECOVERY"),
            ):
                leases.release(
                    "FRACTION",
                    locks,
                    expected_generation=1,
                    candidate_path=terminal,
                    repo_root=root,
                )
            self.assertEqual(4, clock.call_count)
            self.assertEqual(
                "ACTIVE", json.loads((locks / "FRACTION.lease.json").read_text())["state"]
            )
            self.assertEqual([], list(locks.glob("*.tmp")))

    def test_postpublication_error_is_uncertain_and_never_retry_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, paths = v2_candidates(root, "MCP-FAULT")
            locks = root / "locks"
            target = locks / "MCP-FAULT.lease.json"
            real_sync = util._fsync_directory

            def fail_after_publication(path):
                if path == locks and target.exists():
                    raise OSError("synthetic postpublication directory sync failure")
                return real_sync(path)

            async def exercise():
                async with Client(mcp_server.create_server(locks, root)) as client:
                    with mock.patch.object(
                        util, "_fsync_directory", side_effect=fail_after_publication
                    ):
                        result = await client.call_tool(
                            "admit_writer", {"candidate_path": str(paths["MCP-FAULT"])}
                        )
                    self.assertEqual("UNCERTAIN", result.structured_content["disposition"])
                    self.assertFalse(result.structured_content["automatic_retry"])
                    self.assertTrue(target.exists())
                    self.assertEqual(
                        "ACTIVE", result.structured_content["custody"]["ownership_status"]
                    )

            asyncio.run(exercise())

    def test_unknown_observation_cannot_be_reported_ok(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, _, paths = v2_candidates(root, "MCP-UNKNOWN")
            locks = root / "locks"

            async def exercise():
                async with Client(mcp_server.create_server(locks, root)) as client:
                    with mock.patch.object(
                        mcp_server,
                        "observe",
                        return_value={"ownership_status": "UNKNOWN_FAIL_CLOSED"},
                    ):
                        result = await client.call_tool(
                            "admit_writer", {"candidate_path": str(paths["MCP-UNKNOWN"])}
                        )
                    self.assertEqual("UNCERTAIN", result.structured_content["disposition"])
                    self.assertFalse(result.structured_content["automatic_retry"])
                    self.assertTrue((locks / "MCP-UNKNOWN.lease.json").is_file())

            asyncio.run(exercise())

    def test_staging_orphan_blocks_new_mutation_without_automatic_cleanup(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as stage:
            root, staging = Path(directory), Path(stage)
            _, _, paths = v2_candidates(root, "MCP-ORPHAN")
            locks = root / "locks"
            domain = hashlib.sha256(
                (str(locks.resolve()) + "\0" + str(root.resolve())).encode()
            ).hexdigest()[:24]
            orphan = staging / f"clh-mcp-{domain}-known-fixture"
            orphan.mkdir()

            async def exercise():
                server = mcp_server.create_server(locks, root, staging_root=staging)
                async with Client(server) as client:
                    result = await client.call_tool(
                        "admit_writer", {"candidate_path": str(paths["MCP-ORPHAN"])}
                    )
                    self.assertEqual("UNCERTAIN", result.structured_content["disposition"])
                    self.assertFalse(result.structured_content["automatic_retry"])
                    self.assertTrue(orphan.is_dir())
                    self.assertFalse((locks / "MCP-ORPHAN.lease.json").exists())
                    self.assertEqual([orphan], list(staging.iterdir()))
                    self.assertTrue((locks / ".mcp-operation.pending").is_dir())
                with tempfile.TemporaryDirectory() as other:
                    second = mcp_server.create_server(locks, root, staging_root=Path(other))
                    async with Client(second) as client:
                        with mock.patch.object(mcp_server, "acquire") as acquire:
                            result = await client.call_tool(
                                "admit_writer", {"candidate_path": str(paths["MCP-ORPHAN"])}
                            )
                            acquire.assert_not_called()
                        self.assertEqual("UNCERTAIN", result.structured_content["disposition"])
                        self.assertFalse(result.structured_content["automatic_retry"])
                        self.assertTrue(orphan.is_dir())
                        self.assertTrue((locks / ".mcp-operation.pending").is_dir())

            asyncio.run(exercise())

    def test_candidate_snapshot_is_outside_source_and_cleans_on_success(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as stage:
            root, staging = Path(directory), Path(stage)
            _, _, paths = v2_candidates(root, "MCP-STAGE")
            locks = root / "locks"
            acquire = mcp_server.acquire
            observed = []

            def inspect_snapshot(candidate, *args, **kwargs):
                self.assertFalse(candidate.is_relative_to(root))
                self.assertTrue(candidate.is_relative_to(staging))
                observed.append(candidate)
                return acquire(candidate, *args, **kwargs)

            async def exercise():
                server = mcp_server.create_server(locks, root, staging_root=staging)
                async with Client(server) as client:
                    with mock.patch.object(mcp_server, "acquire", side_effect=inspect_snapshot):
                        result = await client.call_tool(
                            "admit_writer", {"candidate_path": str(paths["MCP-STAGE"])}
                        )
                    self.assertEqual("OK", result.structured_content["disposition"])
                    self.assertEqual(1, len(observed))
                    self.assertFalse(observed[0].exists())
                    self.assertEqual([], list(staging.iterdir()))

            asyncio.run(exercise())

    def test_unknown_inspections_are_never_labelled_ok(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            async def exercise():
                server = mcp_server.create_server(root / "locks", root)
                async with Client(server) as client:
                    with mock.patch.object(
                        mcp_server,
                        "observe",
                        return_value={"ownership_status": "UNKNOWN_FAIL_CLOSED"},
                    ):
                        for tool in ("inspect_authority", "inspect_custody"):
                            result = await client.call_tool(tool, {"lease_id": "UNKNOWN"})
                            self.assertEqual("UNCERTAIN", result.structured_content["disposition"])
                            self.assertFalse(result.structured_content["automatic_retry"])

            asyncio.run(exercise())

    def test_pending_operation_survives_a_staging_root_change(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, stage = base / "repo", base / "different-stage"
            root.mkdir()
            stage.mkdir()
            _, _, paths = v2_candidates(root, "MCP-PENDING")
            locks = root / "locks"
            pending = locks / ".mcp-operation.pending"
            pending.mkdir(parents=True)

            async def exercise():
                server = mcp_server.create_server(locks, root, staging_root=stage)
                async with Client(server) as client:
                    result = await client.call_tool(
                        "admit_writer", {"candidate_path": str(paths["MCP-PENDING"])}
                    )
                    self.assertEqual("UNCERTAIN", result.structured_content["disposition"])
                    self.assertTrue(pending.is_dir())
                    self.assertFalse((locks / "MCP-PENDING.lease.json").exists())

            asyncio.run(exercise())

    def test_two_mcp_processes_share_one_atomic_operation_fence(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root, stage_a, stage_b = base / "repo", base / "stage-a", base / "stage-b"
            for path in (root, stage_a, stage_b):
                path.mkdir()
            _, _, paths = v2_candidates(root, "MCP-RACE")
            locks = root / "locks"
            ready, proceed = base / "ready", base / "proceed"
            code = r"""import asyncio,json,sys,time
from pathlib import Path
from mcp import Client
from coordination_loop_harness import mcp_server
root,stage,candidate,ready,proceed=map(Path,sys.argv[1:6])
if sys.argv[6]=='hold':
    acquire=mcp_server.acquire
    def paused(*args,**kwargs):
        ready.write_text('entered')
        end=time.monotonic()+90
        while not proceed.exists():
            if time.monotonic()>end:raise RuntimeError('fixture coordination timeout')
            time.sleep(.05)
        return acquire(*args,**kwargs)
    mcp_server.acquire=paused
async def exercise():
    async with Client(mcp_server.create_server(root/'locks',root,staging_root=stage)) as client:
        result=await client.call_tool('admit_writer',{'candidate_path':str(candidate)})
        print(json.dumps(result.structured_content),flush=True)
asyncio.run(exercise())"""
            arguments = [str(root), str(stage_a), str(paths["MCP-RACE"]), str(ready), str(proceed)]
            child = subprocess.Popen(
                [sys.executable, "-B", "-c", code, *arguments, "hold"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                end = time.monotonic() + 30
                while not ready.exists():
                    if child.poll() is not None or time.monotonic() > end:
                        self.fail("first MCP process did not enter its fenced mutation")
                    time.sleep(0.05)
                second = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        code,
                        str(root),
                        str(stage_b),
                        str(paths["MCP-RACE"]),
                        str(ready),
                        str(proceed),
                        "run",
                    ],
                    capture_output=True,
                    timeout=30,
                    check=True,
                )
                self.assertEqual("UNCERTAIN", json.loads(second.stdout)["disposition"])
                self.assertFalse((locks / "MCP-RACE.lease.json").exists())
                proceed.write_text("continue")
                stdout, stderr = child.communicate(timeout=60)
                self.assertEqual(0, child.returncode, stderr)
                self.assertEqual("OK", json.loads(stdout)["disposition"])
                self.assertFalse((locks / ".mcp-operation.pending").exists())
            finally:
                proceed.write_text("stop waiting")
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=10)

    def test_inspection_io_failure_is_uncertain_without_retry_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            async def exercise():
                async with Client(mcp_server.create_server(root / "locks", root)) as client:
                    with mock.patch.object(
                        mcp_server,
                        "observe",
                        side_effect=PermissionError("fixture root unavailable"),
                    ):
                        for tool in ("inspect_authority", "inspect_custody"):
                            result = await client.call_tool(tool, {"lease_id": "UNAVAILABLE"})
                            self.assertEqual("UNCERTAIN", result.structured_content["disposition"])
                            self.assertFalse(result.structured_content["automatic_retry"])
                            self.assertEqual(
                                "UNKNOWN_FAIL_CLOSED",
                                result.structured_content["custody"]["ownership_status"],
                            )

            asyncio.run(exercise())

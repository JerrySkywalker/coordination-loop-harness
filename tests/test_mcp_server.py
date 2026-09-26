from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mcp import Client

from coordination_loop_harness import mcp_server
from coordination_loop_harness.mcp_server import create_server

ROOT = Path(__file__).resolve().parents[1]


def legacy_lease() -> dict[str, object]:
    return {
        "schema_version": "coord.repo-set-lease.v1",
        "lease_id": "MCP-RUN",
        "run_id": "MCP-RUN",
        "state": "ACTIVE",
        "generation": 1,
        "created_utc": "2026-01-01T00:00:00Z",
        "owner": "fixture",
        "coordination_repository": "example/program",
        "repositories": [
            {
                "repository": "example/product",
                "mode": "WRITE",
                "canonical_path": None,
                "worktree_root": None,
                "exact_sha": None,
            }
        ],
        "local_scopes": [],
        "infrastructure_scopes": [],
        "active_writer_repository": "example/product",
        "decision_ref": None,
        "released_utc": None,
        "outcome_ref": None,
    }


def admitted_candidate(root: Path) -> Path:
    shutil.copytree(ROOT / "schemas", root / "schemas")
    shutil.copy(ROOT / "TEMPLATE_VERSION", root / "TEMPLATE_VERSION")
    candidate = legacy_lease()
    decision = root / "decisions" / "MCP-RUN" / "DEC-MCP-RUN-1.json"
    decision.parent.mkdir(parents=True)
    markdown = decision.with_suffix(".md")
    markdown.write_text("# fixture\n", encoding="utf-8", newline="")
    candidate["decision_ref"] = decision.relative_to(root).as_posix()
    decision.write_text(
        json.dumps(
            {
                "schema_version": "coord.decision.v2",
                "decision_id": "DEC-MCP-RUN-1",
                "run_id": "MCP-RUN",
                "sequence": 1,
                "decision_type": "SCOPE_CHANGE",
                "status": "ACCEPTED",
                "issued_by": "fixture",
                "issued_utc": "2026-01-01T00:00:00Z",
                "decision": "Authorize fixture admission.",
                "rationale": "MCP facade test.",
                "scope": ["example/program", "example/product"],
                "conditions": [],
                "authorized_actions": ["lease:acquire"],
                "lease_id": "MCP-RUN",
                "lease_generation": 1,
                "previous_decision_ref": None,
                "markdown_sha256": hashlib.sha256(markdown.read_bytes()).hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    path = root / "candidate.json"
    path.write_text(json.dumps(candidate), encoding="utf-8")
    return path


def v2_candidates(root: Path, *lease_ids: str) -> tuple[object, dict[str, dict], dict[str, Path]]:
    # Reuse the core lease fixture so the MCP test crosses real Git, decision,
    # digest, custody, and terminal-release guards instead of mocking them out.
    from test_leases import LeaseTests

    fixture = LeaseTests()
    repository = fixture.repository_worktree(root, "product", repository="example/product")
    candidates = {}
    paths = {}
    for lease_id in lease_ids:
        candidate = fixture.lease_v2(lease_id, "example/product", *repository)
        candidate["expires_utc"] = "2099-01-01T00:00:00Z"
        fixture.authorize(root, candidate, "lease:acquire")
        path = root / f"{lease_id}-active.json"
        fixture.write(path, candidate)
        candidates[lease_id] = candidate
        paths[lease_id] = path
    return fixture, candidates, paths


class McpServerTests(unittest.TestCase):
    def test_discovers_tools_and_refuses_v1_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            locks = root / "locks"
            locks.mkdir()
            server = create_server(locks, root)

            async def exercise() -> None:
                async with Client(server) as client:
                    tools = await client.list_tools()
                    self.assertEqual(
                        {"inspect_authority", "admit_writer", "inspect_custody", "release_writer"},
                        {tool.name for tool in tools.tools},
                    )
                    candidate = await client.call_tool(
                        "admit_writer", {"candidate_path": str(admitted_candidate(root))}
                    )
                    self.assertEqual("REFUSED", candidate.structured_content["disposition"])
                    self.assertFalse((locks / "MCP-RUN.lease.json").exists())
                    missing = await client.call_tool("inspect_authority", {"lease_id": "missing"})
                    self.assertEqual("REFUSED", missing.structured_content["disposition"])
                    stale = await client.call_tool(
                        "release_writer",
                        {
                            "lease_id": "MCP-RUN",
                            "expected_generation": 99,
                            "terminal_candidate_path": str(root / "terminal.json"),
                        },
                    )
                    self.assertEqual("REFUSED", stale.structured_content["disposition"])
                    self.assertFalse((locks / "MCP-RUN.lease.json").exists())

            asyncio.run(exercise())

    def test_facade_created_v2_writer_releases_exact_lease_once(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture, active, paths = v2_candidates(root, "MCP-RUN")
            locks = root / "locks"
            server = create_server(locks, root)

            async def exercise() -> None:
                async with Client(server) as client:
                    admitted = await client.call_tool(
                        "admit_writer", {"candidate_path": str(paths["MCP-RUN"])}
                    )
                    self.assertEqual("OK", admitted.structured_content["disposition"])
                    self.assertEqual("MCP-RUN", admitted.structured_content["identity"]["lease_id"])
                    self.assertEqual(
                        "MCP-RUN",
                        json.loads(admitted.structured_content["serialized_authority"])["lease_id"],
                    )
                    custody = await client.call_tool("inspect_custody", {"lease_id": "MCP-RUN"})
                    self.assertEqual("OK", custody.structured_content["disposition"])
                    self.assertFalse(custody.structured_content["custody"]["automatic_reclaim"])
                    replay = await client.call_tool(
                        "admit_writer", {"candidate_path": str(paths["MCP-RUN"])}
                    )
                    self.assertEqual("REFUSED", replay.structured_content["disposition"])
                    _, terminal_path = fixture.terminal_v2(
                        root,
                        active["MCP-RUN"],
                        authority="NORMAL",
                        released_utc="2026-09-04T00:30:00Z",
                    )
                    with mock.patch(
                        "coordination_loop_harness.leases.utc_now",
                        return_value="2026-09-04T00:30:01Z",
                    ):
                        closed = await client.call_tool(
                            "release_writer",
                            {
                                "lease_id": "MCP-RUN",
                                "expected_generation": 1,
                                "terminal_candidate_path": str(terminal_path),
                            },
                        )
                    self.assertEqual("OK", closed.structured_content["disposition"])
                    self.assertEqual("MCP-RUN", closed.structured_content["identity"]["lease_id"])
                    self.assertEqual(
                        "RELEASED", json.loads((locks / "MCP-RUN.lease.json").read_text())["state"]
                    )
                    again = await client.call_tool(
                        "release_writer",
                        {
                            "lease_id": "MCP-RUN",
                            "expected_generation": 1,
                            "terminal_candidate_path": str(terminal_path),
                        },
                    )
                    self.assertEqual("REFUSED", again.structured_content["disposition"])

            asyncio.run(exercise())

    def test_candidate_swap_cannot_change_acquired_or_reported_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, paths = v2_candidates(root, "LEASE-A", "LEASE-B")
            locks = root / "locks"
            server = create_server(locks, root)
            real_acquire = mcp_server.acquire

            def swap_then_acquire(candidate: Path, lock_root: Path, **kwargs: object) -> Path:
                paths["LEASE-A"].write_bytes(paths["LEASE-B"].read_bytes())
                return real_acquire(candidate, lock_root, **kwargs)

            async def exercise() -> None:
                async with Client(server) as client:
                    with mock.patch.object(mcp_server, "acquire", side_effect=swap_then_acquire):
                        admitted = await client.call_tool(
                            "admit_writer", {"candidate_path": str(paths["LEASE-A"])}
                        )
                    self.assertEqual("OK", admitted.structured_content["disposition"])
                    self.assertEqual("LEASE-A", admitted.structured_content["identity"]["lease_id"])
                    self.assertEqual(
                        "LEASE-A",
                        json.loads(admitted.structured_content["serialized_authority"])["lease_id"],
                    )
                    self.assertTrue((locks / "LEASE-A.lease.json").exists())
                    self.assertFalse((locks / "LEASE-B.lease.json").exists())

            asyncio.run(exercise())

    def test_candidate_swap_to_v1_cannot_change_v2_admission(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, _, paths = v2_candidates(root, "LEASE-A")
            locks = root / "locks"
            server = create_server(locks, root)
            real_acquire = mcp_server.acquire

            def swap_then_acquire(candidate: Path, lock_root: Path, **kwargs: object) -> Path:
                paths["LEASE-A"].write_text(json.dumps(legacy_lease()), encoding="utf-8")
                return real_acquire(candidate, lock_root, **kwargs)

            async def exercise() -> None:
                async with Client(server) as client:
                    with mock.patch.object(mcp_server, "acquire", side_effect=swap_then_acquire):
                        result = await client.call_tool(
                            "admit_writer", {"candidate_path": str(paths["LEASE-A"])}
                        )
                    self.assertEqual("OK", result.structured_content["disposition"])
                    self.assertEqual("LEASE-A", result.structured_content["identity"]["lease_id"])
                    self.assertEqual(
                        ["LEASE-A.lease.json"], [p.name for p in locks.glob("*.lease.json")]
                    )

            asyncio.run(exercise())

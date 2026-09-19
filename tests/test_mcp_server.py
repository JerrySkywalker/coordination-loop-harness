from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from mcp import Client

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


class McpServerTests(unittest.TestCase):
    def test_discovers_tools_and_refuses_unknown_or_stale_release(self) -> None:
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
                    self.assertEqual("OK", candidate.structured_content["disposition"])
                    custody = await client.call_tool("inspect_custody", {"lease_id": "MCP-RUN"})
                    self.assertEqual("OK", custody.structured_content["disposition"])
                    self.assertTrue(
                        custody.structured_content["custody"]["automatic_reclaim"] is False
                    )
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
                    self.assertEqual(
                        "ACTIVE", json.loads((locks / "MCP-RUN.lease.json").read_text())["state"]
                    )

            asyncio.run(exercise())

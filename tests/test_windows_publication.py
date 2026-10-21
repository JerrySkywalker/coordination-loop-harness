"""SOURCE_TEST: temporary Git/real file bytes; synthetic uninstalled OS custody."""

import dataclasses
import os
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from unittest import mock

import test_leases as leases_fixture
import test_windows_custody as custody_fixture
import test_windows_issuer as issuer_fixture

from coordination_loop_harness.util import sha256_bytes
from coordination_loop_harness.windows_capability import WindowsHandoffSession
from coordination_loop_harness.windows_issuer import WriterTarget
from coordination_loop_harness.windows_publication import (
    RESULT_CLAIM,
    RESULT_HANDOFF,
    RESULT_PATH,
    ResultPublication,
)
from coordination_loop_harness.windows_security import WindowsBoundaryHold
from coordination_loop_harness.windows_writer import check_writer_snapshot


class ResultPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.git = lambda *args: leases_fixture.LeaseTests.git(self.root, *args)
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Source Test")
        self.git("config", "user.email", "source@example.invalid")
        (self.root / "README.md").write_bytes(b"unchanged baseline\n")
        self.git("add", "README.md")
        self.git("commit", "-m", "baseline")
        self.current = leases_fixture.LeaseTests.lease_v2(
            "RESULT-ONE",
            "local/result",
            self.root,
            self.root,
            "main",
            self.git("rev-parse", "HEAD"),
        )
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # These mocks represent absent deployment qualification, not OS grants.
        self.stack.enter_context(
            mock.patch("coordination_loop_harness.windows_publication.WindowsSecurityReader")
        ).return_value.file_security.return_value = dataclasses.replace(
            custody_fixture.SECURITY, owner=issuer_fixture.CLIENT.sid
        )
        self.seal = self.stack.enter_context(
            mock.patch(
                "coordination_loop_harness.windows_publication.sealed_decision",
                side_effect=lambda _path: nullcontext(),
            )
        )

    def publication(self):
        return self.stack.enter_context(
            ResultPublication(
                self.current["repositories"][0], publisher_sid=issuer_fixture.CLIENT.sid
            )
        )

    def publish(self, raw=b"local result\n"):
        path = self.root / RESULT_PATH
        path.parent.mkdir(exist_ok=True)
        with path.open("xb") as stream:
            stream.write(raw)
        return path

    def test_exact_file_observation_is_pinned_and_cannot_reverse_phase(self):
        publication = self.publication()
        raw = "结果\n".encode()
        self.publish(raw)
        proof = publication.verify(self.current)
        self.assertEqual(
            {"path": RESULT_PATH, "size": len(raw), "sha256": sha256_bytes(raw)}, proof
        )
        self.assertEqual(proof, publication.verify(self.current))
        proof["sha256"] = "0" * 64
        self.assertNotEqual(proof, publication.verify(self.current))
        self.seal.assert_called_once()
        with self.assertRaisesRegex(WindowsBoundaryHold, "CANNOT_REVERSE"):
            publication.check_clean()

    def test_create_new_and_missing_publication_refuse(self):
        publication = self.publication()
        with self.assertRaisesRegex(WindowsBoundaryHold, "SOLE_EXACT_EFFECT"):
            publication.verify(self.current)
        self.publish()
        with self.assertRaisesRegex(WindowsBoundaryHold, "CREATE_NEW"):
            self.publication()

    def test_invalid_utf8_and_8193_bytes_refuse(self):
        for raw, reason in ((b"\xff", "INVALID_UTF8"), (b"x" * 8193, "TOO_LARGE")):
            with self.subTest(reason=reason):
                publication = self.publication()
                path = self.publish(raw)
                with self.assertRaisesRegex(WindowsBoundaryHold, reason):
                    publication.verify(self.current)
                path.unlink()

    def test_8192_bytes_is_the_finite_limit(self):
        publication = self.publication()
        self.publish(b"x" * 8192)
        self.assertEqual(8192, publication.verify(self.current)["size"])

    def test_extra_or_ignored_side_output_refuses(self):
        for ignored in (False, True):
            with self.subTest(ignored=ignored):
                publication = self.publication()
                path = self.publish()
                extra = self.root / ("hidden.tmp" if ignored else "extra.txt")
                if ignored:
                    (self.root / ".git" / "info" / "exclude").write_text("hidden.tmp\n")
                extra.write_bytes(b"unadmitted")
                with self.assertRaisesRegex(WindowsBoundaryHold, "SOLE_EXACT_EFFECT"):
                    publication.verify(self.current)
                extra.unlink()
                path.unlink()

    def test_non_origin_remote_is_not_local_only(self):
        publication = self.publication()
        self.publish()
        self.git("remote", "add", "other", "https://example.invalid/no-access")
        with self.assertRaisesRegex(WindowsBoundaryHold, "ZERO_REMOTES"):
            publication.verify(self.current)

    def test_post_effect_refuses_an_additional_read_repository(self):
        publication = self.publication()
        changed = dict(
            self.current,
            repositories=[
                self.current["repositories"][0],
                dict(self.current["repositories"][0], repository="local/read", mode="READ"),
            ],
        )
        with self.assertRaisesRegex(WindowsBoundaryHold, "WRITER_CHANGED"):
            publication.verify(changed)

    def test_worktree_configuration_presence_and_content_are_pinned(self):
        self.git("config", "extensions.worktreeConfig", "true")
        self.git("config", "--worktree", "source.fixture", "before")
        publication = self.publication()
        path = self.publish()
        self.git("config", "--worktree", "source.fixture", "after")
        with self.assertRaisesRegex(WindowsBoundaryHold, "METADATA_CHANGED"):
            publication.verify(self.current)
        path.unlink()
        config = self.root / ".git" / "config.worktree"
        config.unlink()
        publication = self.publication()
        self.publish()
        self.git("config", "--worktree", "source.fixture", "new-file")
        with self.assertRaisesRegex(WindowsBoundaryHold, "METADATA_CHANGED"):
            publication.verify(self.current)

    def test_index_head_config_refs_and_git_lock_changes_refuse(self):
        publication = self.publication()
        path = self.publish()
        lock = self.root / ".git" / "index.lock"
        lock.write_bytes(b"another writer")
        with self.assertRaisesRegex(WindowsBoundaryHold, "ACTIVE_GIT_LOCK"):
            publication.verify(self.current)
        lock.unlink()
        (self.root / "README.md").write_bytes(b"tracked effect")
        with self.assertRaisesRegex(WindowsBoundaryHold, "GIT_BINDING"):
            publication.verify(self.current)
        (self.root / "README.md").write_bytes(b"unchanged baseline\n")
        self.git("config", "result.test", "changed")
        with self.assertRaisesRegex(WindowsBoundaryHold, "METADATA_CHANGED"):
            publication.verify(self.current)
        self.git("config", "--unset", "result.test")
        self.git("branch", "unexpected")
        with self.assertRaisesRegex(WindowsBoundaryHold, "METADATA_CHANGED"):
            publication.verify(self.current)
        self.git("branch", "-D", "unexpected")
        self.git("add", RESULT_PATH)
        with self.assertRaisesRegex(WindowsBoundaryHold, "GIT_BINDING"):
            publication.verify(self.current)
        self.git("commit", "-m", "forbidden source test drift")
        self.assertTrue(path.exists())
        with self.assertRaisesRegex(WindowsBoundaryHold, "GIT_BINDING"):
            publication.verify(self.current)

    def test_file_and_writer_substitution_refuse(self):
        publication = self.publication()
        path = self.publish()
        publication.verify(self.current)
        path.write_bytes(b"tampered")
        with self.assertRaisesRegex(WindowsBoundaryHold, "OBSERVATION_CHANGED"):
            publication.verify(self.current)
        changed = dict(
            self.current, repositories=[dict(self.current["repositories"][0], mode="READ")]
        )
        with self.assertRaisesRegex(WindowsBoundaryHold, "WRITER_CHANGED"):
            publication.verify(changed)

    def test_hardlink_and_case_alias_refuse(self):
        publication = self.publication()
        parent = self.root / "M1"
        parent.mkdir()
        os.link(self.root / "README.md", parent / "result.md")
        with self.assertRaisesRegex(WindowsBoundaryHold, "TYPE_OR_ALIAS"):
            publication.verify(self.current)
        (parent / "result.md").unlink()
        (parent / "RESULT.md").write_bytes(b"wrong case")
        with self.assertRaises(WindowsBoundaryHold):
            publication.verify(self.current)

    def test_result_requires_terminal_parent_and_zero_active_job(self):
        fixture = custody_fixture
        for job in (
            fixture.JOB,
            dataclasses.replace(fixture.JOB, participant_exit=1, active_count=0, active_pids=()),
        ):
            with (
                self.subTest(job=job),
                self.assertRaisesRegex(WindowsBoundaryHold, "TERMINAL_WRITER"),
            ):
                check_writer_snapshot(
                    fixture.TOKEN, job, fixture.BINDING, fixture.INTENT, terminal=True
                )
        terminal = dataclasses.replace(
            fixture.JOB, participant_exit=0, active_count=0, active_pids=()
        )
        observed = check_writer_snapshot(
            fixture.TOKEN, terminal, fixture.BINDING, fixture.INTENT, terminal=True
        )
        self.assertFalse(observed["custody_resolved"])

    def issuer_fixture(self):
        fixture = issuer_fixture.WindowsIssuerSourceTests(
            "test_exact_target_and_source_only_handoff"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        chain = fixture.fixture
        chain.candidate["repositories"] = self.current["repositories"]
        chain.candidate["active_writer_repository"] = "local/result"
        leases_fixture.LeaseTests().authorize(chain.root, chain.candidate, "lease:acquire")
        chain.write_candidate()
        chain.verifier()
        binding = dataclasses.replace(fixture.binding, lease_sha256=sha256_bytes(chain.serialized))
        issuer = fixture.new_issuer(
            deadline=chain.deadline,
            serialized_lease=chain.serialized,
            target=WriterTarget.from_lease(self.current["repositories"][0]),
        )
        return fixture, issuer, binding

    def test_real_lease_chain_and_versioned_issuer_post_effect_then_public_v1_refusal(self):
        fixture, issuer, binding = self.issuer_fixture()
        owner = self

        class PublishingPipe(issuer_fixture.FakePipe):
            def write_frame(self, data):
                super().write_frame(data)
                if b'"stage":"effect_publication"' in data:
                    owner.publish()

        from coordination_loop_harness.util import canonical_json_bytes

        pipe = PublishingPipe(canonical_json_bytes(issuer._expected_request(result=True)))
        pipe.binding = binding
        with (
            mock.patch(
                "coordination_loop_harness.windows_issuer.WindowsPipe.first_server",
                return_value=pipe,
            ),
            mock.patch(
                "coordination_loop_harness.windows_issuer.sealed_decision",
                side_effect=lambda _path: nullcontext(),
            ),
            mock.patch(
                "coordination_loop_harness.windows_issuer.WindowsWriterInspector"
            ) as inspector,
        ):
            response = issuer.claim_result_once()
        self.assertEqual(RESULT_HANDOFF, response["schema_version"])
        self.assertFalse(response["custody_resolved"])
        import json

        acks = [json.loads(frame) for frame in pipe.sent]
        effects = [ack["published_result"] for ack in acks if "published_result" in ack]
        self.assertEqual(2, len(effects))
        self.assertEqual(effects[0], effects[1])
        self.assertEqual(sha256_bytes(b"local result\n"), effects[0]["sha256"])
        terminal_calls = [
            call
            for call in inspector.return_value.observe.call_args_list
            if call.kwargs.get("terminal")
        ]
        self.assertEqual(3, len(terminal_calls))
        # The existing public verifier still refuses this exact published file.
        verifier = fixture.fixture.verifier()
        with mock.patch.object(verifier, "_protected_tree", return_value={}):
            with self.assertRaisesRegex(ValueError, "untracked"):
                verifier.verify("LOCAL-T1", 1, fixture.fixture.serialized)
        self.assertTrue((self.root / RESULT_PATH).exists())

    def test_v1_cannot_accept_result_claim_or_silently_upgrade(self):
        _fixture, issuer, binding = self.issuer_fixture()
        from coordination_loop_harness.util import canonical_json_bytes

        pipe = issuer_fixture.FakePipe(canonical_json_bytes(issuer._expected_request(result=True)))
        pipe.binding = binding
        with (
            mock.patch(
                "coordination_loop_harness.windows_issuer.WindowsPipe.first_server",
                return_value=pipe,
            ),
            mock.patch(
                "coordination_loop_harness.windows_issuer.sealed_decision",
                side_effect=lambda _path: nullcontext(),
            ),
            self.assertRaisesRegex(WindowsBoundaryHold, "EXACT_CLAIM"),
        ):
            issuer.claim_once()
        self.assertEqual([], pipe.sent)
        with self.assertRaisesRegex(WindowsBoundaryHold, "ALREADY_ATTEMPTED"):
            issuer.claim_result_once()

    def test_result_claim_refuses_a_real_additional_read_repository(self):
        fixture, _unused, binding = self.issuer_fixture()
        helper = leases_fixture.LeaseTests()
        chain = fixture.fixture
        extra = helper.repository_worktree(chain.root, "extra-read", repository="local/read")
        read_item = helper.lease_v2("READ", "local/read", *extra, mode="READ")["repositories"][0]
        chain.candidate["repositories"].append(read_item)
        helper.authorize(chain.root, chain.candidate, "lease:acquire")
        chain.write_candidate()
        chain.verifier()
        issuer = fixture.new_issuer(
            deadline=chain.deadline,
            serialized_lease=chain.serialized,
            target=WriterTarget.from_lease(self.current["repositories"][0]),
        )
        from coordination_loop_harness.util import canonical_json_bytes

        pipe = issuer_fixture.FakePipe(canonical_json_bytes(issuer._expected_request(result=True)))
        pipe.binding = binding
        with (
            mock.patch(
                "coordination_loop_harness.windows_issuer.WindowsPipe.first_server",
                return_value=pipe,
            ),
            mock.patch(
                "coordination_loop_harness.windows_issuer.sealed_decision",
                side_effect=lambda _path: nullcontext(),
            ),
            self.assertRaisesRegex(WindowsBoundaryHold, "SINGLE_REPOSITORY"),
        ):
            issuer.claim_result_once()
        self.assertEqual([], pipe.sent)

    def test_lease_tamper_after_effect_keeps_file_and_refuses_receipt(self):
        fixture, issuer, binding = self.issuer_fixture()
        owner = self

        class TamperingPipe(issuer_fixture.FakePipe):
            def write_frame(self, data):
                super().write_frame(data)
                if b'"stage":"effect_publication"' in data:
                    owner.publish()
                    (fixture.fixture.locks / "LOCAL-T1.lease.json").write_bytes(b"{}\n")

        from coordination_loop_harness.util import canonical_json_bytes

        pipe = TamperingPipe(canonical_json_bytes(issuer._expected_request(result=True)))
        pipe.binding = binding
        with (
            mock.patch(
                "coordination_loop_harness.windows_issuer.WindowsPipe.first_server",
                return_value=pipe,
            ),
            mock.patch(
                "coordination_loop_harness.windows_issuer.sealed_decision",
                side_effect=lambda _path: nullcontext(),
            ),
            mock.patch("coordination_loop_harness.windows_issuer.WindowsWriterInspector"),
            self.assertRaisesRegex(ValueError, "differs"),
        ):
            issuer.claim_result_once()
        self.assertTrue((self.root / RESULT_PATH).exists())
        self.assertFalse(any(b'"stage":"receipt"' in frame for frame in pipe.sent))
        with self.assertRaisesRegex(WindowsBoundaryHold, "ALREADY_ATTEMPTED"):
            issuer.claim_result_once()

    @unittest.skipUnless(os.name == "nt", "read-only seal on a test-owned ordinary file")
    def test_native_seal_denies_later_write_without_claiming_acl_qualification(self):
        from coordination_loop_harness.windows_custody import sealed_decision
        from coordination_loop_harness.windows_security import WindowsSecurityReader

        path = self.publish()
        with sealed_decision(path):
            self.assertTrue(WindowsSecurityReader().file_security(path).identity)
            with self.assertRaises(OSError):
                path.write_bytes(b"must not change sealed bytes")

    def test_worker_owned_promoted_file_is_not_a_client_publication(self):
        publication = self.publication()
        self.publish()
        publication._reader.file_security.return_value = dataclasses.replace(
            custody_fixture.SECURITY, owner="S-1-5-21-100-200-300-1002"
        )
        self.assertNotEqual(
            issuer_fixture.CLIENT.sid, publication._reader.file_security.return_value.owner
        )
        with self.assertRaisesRegex(WindowsBoundaryHold, "FIXED_CLIENT_OWNER"):
            publication.verify(self.current)


class ResultClientTests(unittest.TestCase):
    def session(self, proof):
        from coordination_loop_harness.util import canonical_json_bytes

        result = {"path": RESULT_PATH, "size": 1, "sha256": "a" * 64}
        pipe = mock.Mock()
        session = WindowsHandoffSession(
            ExitStack(),
            pipe,
            {},
            {"schema_version": RESULT_CLAIM},
            issuer_fixture.CLIENT,
            issuer_fixture.CLIENT.sid,
            custody_fixture.RESTRICTING,
            mock.Mock(),
        )
        session._binding = custody_fixture.BINDING
        session._bound_handles = (1, 2)
        session._next = 2  # SOURCE_TEST: completed prepublication phase.
        ack = {
            "schema_version": "clh.windows-local-checkpoint-ack.v1",
            "stage": "receipt",
            "handoff_sha256": session._handoff_digest,
            "published_result": result | proof,
        }
        pipe.read_frame.return_value = canonical_json_bytes(ack)
        self.addCleanup(session.close)
        return session, pipe

    def test_client_checks_exact_proof_and_keeps_runtime_hold(self):
        session, pipe = self.session({})
        with (
            mock.patch("coordination_loop_harness.windows_capability.require_process_self"),
            mock.patch("coordination_loop_harness.windows_capability.require_client_boot_deadline"),
            mock.patch(
                "coordination_loop_harness.windows_capability.WindowsWriterInspector"
            ) as inspect,
        ):
            inspect.return_value.observe.return_value = {"custody_resolved": False}
            snapshot = session.checkpoint("receipt", participant_handle=1, job_handle=2)
            self.assertFalse(snapshot["custody_resolved"])
            self.assertEqual("a" * 64, snapshot["published_result"]["sha256"])
            inspect.return_value.observe.assert_called_once_with(
                participant_handle=1, job_handle=2, terminal=True
            )
            from coordination_loop_harness.util import canonical_json_bytes

            pipe.read_frame.return_value = canonical_json_bytes(
                {
                    "schema_version": "clh.windows-local-checkpoint-ack.v1",
                    "stage": "dag_ack",
                    "handoff_sha256": session._handoff_digest,
                    "published_result": {"path": RESULT_PATH, "size": 1, "sha256": "b" * 64},
                }
            )
            with self.assertRaisesRegex(WindowsBoundaryHold, "RESULT_ACK"):
                session.checkpoint("dag_ack", participant_handle=1, job_handle=2)

    def test_malformed_or_out_of_scope_proof_latches_session(self):
        for proof in (
            {"path": "other.md"},
            {"size": True},
            {"size": 8193},
            {"sha256": "bad"},
            {"extra": "unadmitted"},
        ):
            session, _pipe = self.session(proof)
            with (
                self.subTest(proof=proof),
                mock.patch("coordination_loop_harness.windows_capability.require_process_self"),
                mock.patch(
                    "coordination_loop_harness.windows_capability.require_client_boot_deadline"
                ),
                mock.patch("coordination_loop_harness.windows_capability.WindowsWriterInspector"),
                self.assertRaisesRegex(WindowsBoundaryHold, "RESULT_ACK"),
            ):
                session.checkpoint("receipt", participant_handle=1, job_handle=2)
            self.assertTrue(session._closed)


if __name__ == "__main__":
    unittest.main()

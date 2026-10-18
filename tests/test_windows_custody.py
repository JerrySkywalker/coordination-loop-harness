"""SOURCE_TEST security/chain negatives; native probes never install protection."""

import ctypes
import dataclasses
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import test_leases as existing

from coordination_loop_harness.local_time import HostSample, LocalDeadline, LocalTimeHold
from coordination_loop_harness.util import canonical_json_bytes, sha256_bytes, sha256_file
from coordination_loop_harness.windows_acl import plan_windows_acls
from coordination_loop_harness.windows_custody import (
    ADMINISTRATORS,
    MANIFEST,
    SYSTEM,
    WindowsIssuerConfig,
    WindowsProtectedCustodyVerifier,
    check_file_security,
    check_observer,
)
from coordination_loop_harness.windows_security import (
    Ace,
    FileSecurity,
    TokenSecurity,
    WindowsBoundaryHold,
    WindowsSecurityReader,
    restricted_writer_token,
)
from coordination_loop_harness.windows_writer import JobSnapshot, check_writer_snapshot

ROOT = Path(__file__).resolve().parents[1]
WORKER = "S-1-5-21-100-200-300-1001"
RESTRICTING = "S-1-5-21-100-200-300-1002"
TOKEN = TokenSecurity(WORKER, (), (RESTRICTING,), ("SeChangeNotifyPrivilege",))
SECURITY = FileSecurity(SYSTEM, True, (Ace(0, 0, 0x1F01FF, SYSTEM),), (1,), "a" * 64)
JOB = JobSnapshot(42, 1000, True, 259, 0x2000, 1, (42,))


class WindowsSecurityPolicyTests(unittest.TestCase):
    def test_acl_positive_is_only_a_synthetic_policy_check(self):
        check_file_security(SECURITY, SYSTEM, require_protected=True)

    def test_owner_acl_null_unsupported_ace_and_nonoperator_write_refused(self):
        for changed in (
            dataclasses.replace(SECURITY, owner=WORKER),
            dataclasses.replace(SECURITY, protected_dacl=False),
            dataclasses.replace(SECURITY, aces=()),
            dataclasses.replace(SECURITY, aces=(Ace(5, 0, 1, SYSTEM),)),
            dataclasses.replace(SECURITY, aces=(Ace(0, 0x80, 1, SYSTEM),)),
            dataclasses.replace(SECURITY, aces=(Ace(0, 0, 2, WORKER),)),
            dataclasses.replace(SECURITY, aces=(Ace(0, 0, 0x40, WORKER),)),
            dataclasses.replace(SECURITY, aces=(Ace(0, 8, 0x40000, WORKER),)),
        ):
            with self.subTest(changed=changed), self.assertRaises(WindowsBoundaryHold):
                check_file_security(changed, SYSTEM, require_protected=True)

    def test_same_principal_admin_enableable_group_and_privileges_refused(self):
        for changed in (
            dataclasses.replace(TOKEN, user=SYSTEM),
            dataclasses.replace(TOKEN, grant_capable_groups=(ADMINISTRATORS,)),
            dataclasses.replace(TOKEN, privileges=("SeRestorePrivilege",)),
            dataclasses.replace(TOKEN, restricting_sids=()),
            dataclasses.replace(TOKEN, restricting_sids=(SYSTEM,)),
            dataclasses.replace(TOKEN, token_type=2),
        ):
            with self.subTest(changed=changed), self.assertRaises(WindowsBoundaryHold):
                check_observer(changed, SYSTEM, restricted_writer=True)

    def test_breakaway_member_identity_descendants_and_job_policy_refused(self):
        for changed in (
            dataclasses.replace(JOB, limit_flags=0),
            dataclasses.replace(JOB, limit_flags=0x2800),
            dataclasses.replace(JOB, limit_flags=0x3000),
            dataclasses.replace(JOB, participant_in_job=False),
            dataclasses.replace(JOB, participant_creation=0),
            dataclasses.replace(JOB, active_count=2),
            dataclasses.replace(JOB, active_pids=(43,)),
            dataclasses.replace(JOB, active_count=2, active_pids=(42, 42)),
            dataclasses.replace(JOB, participant_exit=0),
        ):
            with self.subTest(changed=changed), self.assertRaises(WindowsBoundaryHold):
                check_writer_snapshot(TOKEN, changed, SYSTEM)

    def test_positive_job_and_zero_parent_exit_never_mint_runtime_custody(self):
        for snapshot in (
            JOB,
            dataclasses.replace(JOB, participant_exit=0, active_count=0, active_pids=()),
        ):
            result = check_writer_snapshot(TOKEN, snapshot, SYSTEM)
            self.assertFalse(result["custody_resolved"])
            self.assertIn("DESCENDANT_TERMINALS_UNQUALIFIED", result["runtime_gate"])

    def test_acl_plan_is_pure_and_rejects_sid_injection_and_shared_principals(self):
        plan = plan_windows_acls(issuer_sid=SYSTEM, worker_sid=WORKER, scratch_sid=RESTRICTING)
        self.assertIn(f"(A;OICI;FR;;;{WORKER})", plan.authority_sddl)
        self.assertNotIn(f"FW;;;{WORKER}", plan.authority_sddl)
        self.assertIn(f"(A;OICI;FRFW;;;{RESTRICTING})", plan.scratch_sddl)
        self.assertIn("UNQUALIFIED", plan.runtime_gate)
        for scratch in (WORKER, SYSTEM, "S-1-5-21)(A;;FA;;;WD)"):
            with self.subTest(scratch=scratch), self.assertRaises(WindowsBoundaryHold):
                plan_windows_acls(issuer_sid=SYSTEM, worker_sid=WORKER, scratch_sid=scratch)

    def test_restricted_token_factory_inspects_result_and_always_closes_handles(self):
        def sid_conversion(text, output):
            ctypes.cast(output, ctypes.POINTER(ctypes.c_void_p))[0] = 123
            return 1

        def create_token(*args):
            self.assertEqual(1, args[1])  # DISABLE_MAX_PRIVILEGE
            self.assertEqual(1, args[6])  # one restricting SID, no WRITE_RESTRICTED
            ctypes.cast(args[-1], ctypes.POINTER(ctypes.c_void_p))[0] = 456
            return 1

        a = SimpleNamespace(
            ConvertStringSidToSidW=mock.Mock(side_effect=sid_conversion),
            CreateRestrictedToken=mock.Mock(side_effect=create_token),
        )
        k = SimpleNamespace(CloseHandle=mock.Mock(), LocalFree=mock.Mock())
        reader = SimpleNamespace(
            a=a,
            k=k,
            _require=WindowsSecurityReader._require,
            inspect_token=mock.Mock(return_value=TOKEN),
        )
        with mock.patch(
            "coordination_loop_harness.windows_security.WindowsSecurityReader", return_value=reader
        ):
            with restricted_writer_token(
                base_token=1, scratch_sid=RESTRICTING, issuer_sid=SYSTEM
            ) as handle:
                self.assertEqual(456, handle)
                k.CloseHandle.assert_not_called()
            self.assertEqual(456, k.CloseHandle.call_args.args[0].value)
            k.LocalFree.assert_called_once()
            reader.inspect_token.return_value = dataclasses.replace(TOKEN, restricting_sids=())
            with self.assertRaises(WindowsBoundaryHold):
                with restricted_writer_token(
                    base_token=1, scratch_sid=RESTRICTING, issuer_sid=SYSTEM
                ):
                    self.fail("invalid token must not be yielded")
            self.assertEqual(2, k.CloseHandle.call_count)

    @unittest.skipUnless(os.name == "nt", "read-only Windows negative control")
    def test_native_same_principal_unrestricted_token_is_not_a_root(self):
        reader = WindowsSecurityReader()
        token = reader.token_security()
        with self.assertRaisesRegex(WindowsBoundaryHold, "SAME_OR_PRIVILEGED"):
            check_observer(token, token.user, restricted_writer=True)
        with tempfile.TemporaryDirectory() as tmp:
            # Inspect one test-owned directory; never change its ACL.
            first = reader.file_security(Path(tmp))
            self.assertEqual(first, reader.file_security(Path(tmp)))


class WindowsCustodyChainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        helper = existing.LeaseTests()
        repo = helper.repository_worktree(self.root, "product", repository="local/fixture")
        helper.git(repo[0], "remote", "remove", "origin")
        self.writer = repo[1]
        self.candidate = helper.lease_v2("LOCAL-T1", "local/fixture", *repo)
        self.candidate["expires_utc"] = "2099-01-01T00:00:00Z"
        helper.authorize(self.root, self.candidate, "lease:acquire")
        self.locks = self.root / "locks"
        self.locks.mkdir()
        (self.root / MANIFEST).parent.mkdir(parents=True)
        shutil.copy(ROOT / MANIFEST, self.root / MANIFEST)
        self.config = WindowsIssuerConfig(
            self.root, self.locks, SYSTEM, sha256_file(self.root / MANIFEST)
        )
        self.issuer = {
            "schema_version": "clh-windows-issuance-root.v1",
            "product_owner": "CLH",
            "issuer_sid": SYSTEM,
            "policy": "OPERATOR_FIXED_CLH_ONLY_ISSUANCE",
            "producer_manifest_sha256": self.config.producer_manifest_sha256,
        }
        self.write_issuer()
        self.write_candidate()

    def write_issuer(self):
        (self.root / "CLH-ISSUANCE-ROOT.json").write_text(json.dumps(self.issuer), encoding="utf-8")

    def write_candidate(self):
        self.serialized = canonical_json_bytes(self.candidate)
        (self.locks / "LOCAL-T1.lease.json").write_bytes(self.serialized)

    def verifier(self):
        sample = HostSample("12345678-1234-1234-1234-123456789abc", "42:1000", 100, 100, 100)
        self.deadline = LocalDeadline(
            lambda: sample,
            intent_id="one-shot",
            generation=self.candidate["generation"],
            lease_sha256=sha256_bytes(self.serialized),
            duration_seconds=600,
        )
        with mock.patch("coordination_loop_harness.windows_custody.WindowsSecurityReader"):
            verifier = WindowsProtectedCustodyVerifier(self.config, self.deadline)
        return verifier

    def check(self, **kwargs):
        verifier = self.verifier()
        # Mock ONLY the not-installed OS protection inventory, explicitly SOURCE_TEST.
        with mock.patch.object(verifier, "_protected_tree", return_value={}):
            return verifier.verify("LOCAL-T1", kwargs.get("generation", 1), self.serialized)

    def test_real_v2_lease_decision_chain_is_reused_without_runtime_authority(self):
        result = self.check()
        self.assertFalse(result["custody_resolved"])
        self.assertFalse(result["authenticated_utc"])
        self.assertEqual("LOCAL_BOOT_BOUND", result["time_assurance"])

    def test_fixed_issuer_digest_generation_and_replay_rejected(self):
        verifier = self.verifier()
        with self.assertRaisesRegex(WindowsBoundaryHold, "BINDING_MISMATCH"):
            verifier.verify("LOCAL-T1", 2, self.serialized)
        with self.assertRaises(LocalTimeHold):
            self.deadline.check("receipt", retained_intent=self.deadline.intent)
        self.issuer["issuer_sid"] = WORKER
        self.write_issuer()
        with self.assertRaisesRegex(WindowsBoundaryHold, "ISSUER_OR_PRODUCER"):
            self.check()

    def test_current_candidate_mismatch_and_decision_digest_forgery_rejected(self):
        verifier = self.verifier()
        (self.locks / "LOCAL-T1.lease.json").write_bytes(b"{}\n")
        with mock.patch.object(verifier, "_protected_tree", return_value={}):
            with self.assertRaisesRegex(ValueError, "differs"):
                verifier.verify("LOCAL-T1", 1, self.serialized)
        self.write_candidate()
        path = self.root / self.candidate["decision_ref"]
        data = json.loads(path.read_text(encoding="utf-8"))
        data["lease_candidate_sha256"] = "f" * 64
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 binding mismatch"):
            self.check()

    def test_wrong_actual_head_and_dirty_writer_rejected(self):
        (self.writer / "extra.txt").write_text("unintended effect", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "untracked"):
            self.check()
        helper = existing.LeaseTests()
        helper.git(self.writer, "add", "extra.txt")
        helper.git(self.writer, "commit", "-m", "drift")
        with self.assertRaisesRegex(ValueError, "SHA|sha|head|HEAD"):
            self.check()

    def test_root_mutation_and_source_manifest_mismatch_rejected(self):
        verifier = self.verifier()
        with mock.patch.object(verifier, "_protected_tree", side_effect=[{}, {}, {"changed": ()}]):
            with self.assertRaisesRegex(ValueError, "changed during"):
                verifier.verify("LOCAL-T1", 1, self.serialized)
        (self.root / MANIFEST).write_bytes(b"forged")
        with self.assertRaisesRegex(WindowsBoundaryHold, "ISSUER_OR_PRODUCER"):
            self.check()

    def test_unsupported_backend_and_caller_root_are_hold(self):
        verifier = self.verifier()
        with self.assertRaisesRegex(WindowsBoundaryHold, "CALLER_ROOT"):
            verifier._protected_tree(self.root / "other")
        verifier._reader.token_security.return_value = dataclasses.replace(TOKEN, user=SYSTEM)
        with self.assertRaisesRegex(WindowsBoundaryHold, "SAME_OR_PRIVILEGED"):
            verifier._protected_tree(self.root)


if __name__ == "__main__":
    unittest.main()

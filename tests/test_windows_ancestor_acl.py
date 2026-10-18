"""SOURCE_TEST directory-chain semantics; no account, ACL or reparse mutation."""

import dataclasses
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from coordination_loop_harness.local_time import HostSample, LocalDeadline
from coordination_loop_harness.windows_custody import (
    SYSTEM,
    TRUSTED_INSTALLER,
    WindowsIssuerConfig,
    WindowsProtectedCustodyVerifier,
    _check_ancestor_security,
    check_file_security,
    check_observer,
)
from coordination_loop_harness.windows_security import (
    Ace,
    FileSecurity,
    TokenSecurity,
    WindowsBoundaryHold,
)

USER = "S-1-5-21-100-200-300-1001"
USERS = "S-1-5-32-545"
CREATOR_OWNER = "S-1-3-0"
TOKEN = TokenSecurity(USER, (USERS,), (), ("SeChangeNotifyPrivilege",))
PROTECTED = FileSecurity(SYSTEM, True, (Ace(0, 3, 0x1F01FF, SYSTEM),), (1,), "a" * 64)


def with_aces(*aces, owner=SYSTEM, protected=True):
    return dataclasses.replace(PROTECTED, owner=owner, protected_dacl=protected, aces=aces)


class AncestorAclPolicyTests(unittest.TestCase):
    def test_servicing_owner_and_inherit_only_defaults_are_ancestor_only(self):
        # Windows volume-root shape: exact servicing owner, create-subdirectory
        # for authenticated users, and an IO default for future descendants.
        ancestor = with_aces(
            Ace(0, 3, 0x1F01FF, SYSTEM),
            Ace(0, 0, 4, USERS),
            Ace(0, 0x0B, 0xE0010000, USERS),
            owner=TRUSTED_INSTALLER,
        )
        _check_ancestor_security(ancestor, SYSTEM)
        with self.assertRaisesRegex(WindowsBoundaryHold, "OWNER_OR_PROTECTED"):
            check_file_security(ancestor, SYSTEM, require_protected=True)
        with self.assertRaisesRegex(WindowsBoundaryHold, "ANCESTOR_OWNER"):
            _check_ancestor_security(dataclasses.replace(ancestor, owner=USER), SYSTEM)
        # No service prefix, arbitrary servicing SID or configurable trust list.
        with self.assertRaisesRegex(WindowsBoundaryHold, "ANCESTOR_OWNER"):
            _check_ancestor_security(
                dataclasses.replace(ancestor, owner=TRUSTED_INSTALLER + "-1"), SYSTEM
            )

    def test_programdata_defaults_are_not_child_access(self):
        ancestor = with_aces(
            Ace(0, 3, 0x1F01FF, SYSTEM),
            Ace(0, 0x0B, 0x10000000, CREATOR_OWNER),
            Ace(0, 3, 0x1200A9, USERS),
            Ace(0, 2, 0x116, USERS),
        )
        _check_ancestor_security(ancestor, SYSTEM)
        with self.assertRaisesRegex(WindowsBoundaryHold, "NON_OPERATOR"):
            check_file_security(ancestor, SYSTEM, require_protected=True)

    def test_create_metadata_and_generic_write_need_the_directory_chain(self):
        for mask in (2, 4, 0x10, 0x100, 0x116, 0x40000000):
            with self.subTest(mask=hex(mask)):
                evidence = with_aces(Ace(0, 3, 0x1F01FF, SYSTEM), Ace(0, 0, mask, USERS))
                _check_ancestor_security(evidence, SYSTEM)
                with self.assertRaisesRegex(WindowsBoundaryHold, "NON_OPERATOR"):
                    check_file_security(evidence, SYSTEM, require_protected=True)

    def test_delete_rename_delete_child_security_takeover_and_unknown_stay_denied(self):
        for mask in (0x40, 0x10000, 0x40000, 0x80000, 0x10000000, 0x2000000, 0x1301BF):
            for flags in (0, 3, 0x10):  # effective, inheritable, and inherited
                with self.subTest(mask=hex(mask), flags=flags):
                    evidence = with_aces(
                        Ace(0, 3, 0x1F01FF, SYSTEM), Ace(0, flags, mask, USER)
                    )
                    with self.assertRaisesRegex(WindowsBoundaryHold, "NON_OPERATOR"):
                        _check_ancestor_security(evidence, SYSTEM)
        # A deny before an unsafe allow is not a token-specific admission proof.
        with self.assertRaisesRegex(WindowsBoundaryHold, "NON_OPERATOR"):
            _check_ancestor_security(
                with_aces(Ace(1, 0, 0x40, USER), Ace(0, 0, 0x40, USER)), SYSTEM
            )

    def test_inherit_only_is_not_effective_but_unknown_evidence_still_holds(self):
        for mask in (0x40, 0x10000, 0x40000, 0x10000000):
            evidence = with_aces(Ace(0, 3, 0x1F01FF, SYSTEM), Ace(0, 0x0B, mask, USER))
            _check_ancestor_security(evidence, SYSTEM)
            with self.assertRaises(WindowsBoundaryHold):
                check_file_security(evidence, SYSTEM, require_protected=True)
        for changed in (
            dataclasses.replace(PROTECTED, aces=()),
            dataclasses.replace(PROTECTED, descriptor_sha256="not-a-digest"),
            with_aces(Ace(5, 0x0B, 1, SYSTEM)),
            with_aces(Ace(0, 0x80, 1, SYSTEM)),
        ):
            with self.subTest(changed=changed), self.assertRaises(WindowsBoundaryHold):
                _check_ancestor_security(changed, SYSTEM)

    def test_servicing_identity_is_never_a_worker_or_protected_mutator(self):
        for token in (
            dataclasses.replace(TOKEN, user=TRUSTED_INSTALLER),
            dataclasses.replace(TOKEN, grant_capable_groups=(TRUSTED_INSTALLER,)),
            dataclasses.replace(TOKEN, restricting_sids=(TRUSTED_INSTALLER,)),
        ):
            with self.subTest(token=token), self.assertRaises(WindowsBoundaryHold):
                check_observer(token, SYSTEM, restricted_writer=True)
        with self.assertRaisesRegex(WindowsBoundaryHold, "NON_OPERATOR"):
            check_file_security(
                with_aces(Ace(0, 0, 0x1F01FF, TRUSTED_INSTALLER)), SYSTEM, require_protected=True
            )
        with self.assertRaises(WindowsBoundaryHold):
            WindowsIssuerConfig(Path.cwd(), Path.cwd() / "locks", TRUSTED_INSTALLER, "a" * 64)


class ExistingDirectoryChainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve(strict=True) / "authority"
        self.root.mkdir()
        self.config = WindowsIssuerConfig(self.root, self.root.parent / "locks", SYSTEM, "a" * 64)
        sample = HostSample("12345678-1234-1234-1234-123456789abc", "42:1000", 100, 100, 100)
        deadline = LocalDeadline(
            lambda: sample, intent_id="source-only", generation=1,
            lease_sha256="a" * 64, duration_seconds=600,
        )
        with mock.patch("coordination_loop_harness.windows_custody.WindowsSecurityReader"):
            self.verifier = WindowsProtectedCustodyVerifier(self.config, deadline)
        self.reader = self.verifier._reader
        self.reader.token_security.return_value = TOKEN
        self.security = {self.root: PROTECTED}
        for parent in self.root.parents:
            self.security[parent] = with_aces(
                Ace(0, 0, 0x116, USERS), Ace(0, 0x0B, 0x10000000, CREATOR_OWNER),
                owner=TRUSTED_INSTALLER if parent == Path(parent.anchor) else SYSTEM,
                protected=False,
            )
        self.reader.file_security.side_effect = lambda path: self.security[path]
        self.original_lstat = Path.lstat
        self.reparse = None

    def lstat(self, path):
        value = self.original_lstat(path)
        return SimpleNamespace(
            st_mode=value.st_mode, st_dev=value.st_dev, st_ino=value.st_ino,
            st_file_attributes=0x400 if path == self.reparse else 0,
        )

    def inventory(self):
        with mock.patch.object(Path, "lstat", autospec=True, side_effect=self.lstat):
            return self.verifier._protected_tree(self.root)

    def test_existing_protected_child_is_checked_before_each_parent(self):
        inventory = self.inventory()
        expected = [self.root, *self.root.parents]
        observed = [call.args[0] for call in self.reader.file_security.call_args_list]
        self.assertEqual(expected, observed)
        self.assertEqual(set(map(str, expected)), set(inventory))
        self.assertEqual(set(map(str, expected)), set(self.inventory()))

    def test_missing_or_nondirectory_root_cannot_supply_a_nonempty_chain(self):
        self.root.rmdir()
        with self.assertRaisesRegex(WindowsBoundaryHold, "TRAVERSAL_FAILED"):
            self.inventory()
        self.reader.file_security.assert_not_called()
        self.root.write_text("not a directory", encoding="utf-8")
        with self.assertRaisesRegex(WindowsBoundaryHold, "ROOT_NOT_DIRECTORY"):
            self.inventory()
        self.assertEqual([mock.call(self.root)], self.reader.file_security.call_args_list)

    def test_child_delete_and_parent_delete_child_prevent_chain_admission(self):
        for path, mask in ((self.root, 0x10000), (self.root.parent, 0x40)):
            saved = self.security[path]
            self.security[path] = with_aces(Ace(0, 0, mask, USER))
            with (
                self.subTest(path=path),
                self.assertRaisesRegex(WindowsBoundaryHold, "NON_OPERATOR"),
            ):
                self.inventory()
            self.security[path] = saved

    def test_intermediate_reparse_and_inherited_mutation_are_rejected(self):
        self.reparse = self.root.parent
        with self.assertRaisesRegex(WindowsBoundaryHold, "REPARSE"):
            self.inventory()
        self.reparse = None
        self.security[self.root.parent] = with_aces(Ace(0, 0x10, 0x40, USER))
        with self.assertRaisesRegex(WindowsBoundaryHold, "NON_OPERATOR"):
            self.inventory()

    def test_each_descendant_still_needs_its_own_protected_acl(self):
        child = self.root / "lease.json"
        child.write_text("source-only", encoding="utf-8")
        for security in (
            dataclasses.replace(PROTECTED, protected_dacl=False),
            with_aces(Ace(0, 0x10, 2, USER)),
            with_aces(Ace(0, 0x0B, 0x40000, USER)),
        ):
            self.security[child] = security
            with self.subTest(security=security), self.assertRaises(WindowsBoundaryHold):
                self.inventory()


if __name__ == "__main__":
    unittest.main()

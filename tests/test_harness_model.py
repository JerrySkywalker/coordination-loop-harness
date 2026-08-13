from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from coordination_loop_harness.cli import main
from coordination_loop_harness.harness_model import validate_harness_model, validate_profile_pack
from coordination_loop_harness.validation import load_schema, validate_document

ROOT = Path(__file__).resolve().parents[1]


def model() -> dict[str, object]:
    return {
        "schema_version": "coord.harness-model.v1",
        "model_id": "coord.harness-model.v1",
        "axes": {
            "A": ["A0", "A1", "A2", "A3", "A4", "A5", "OWNER"],
            "B": ["B0", "B1", "B2", "B3", "B4"],
            "P": ["P_INIT", "P0", "P1", "P2", "P3"],
            "V": ["V0", "V1", "V2", "V3", "V4", "V5", "V6", "V7"],
            "E": ["E0", "E1", "E2", "E3", "E4", "E5", "E6"],
            "F": ["F0", "F1", "F2", "F3", "F4", "F5"],
            "G": ["G0", "G1", "G2", "G3", "G4"],
            "L": ["L0", "L1", "L2", "L3", "L4", "L5"],
        },
        "proof_vector_axes": ["V", "E", "F", "G"],
        "budget_ledger": {
            "tuple_fields": ["window_cap", "maximum_renewals", "lifetime_cap"],
            "no_borrow": True,
            "replenishment_requires_lifetime_cap": True,
        },
        "progress_semantics": {
            "initial_states": ["P_INIT"],
            "advancing_states": ["P0", "P1", "P2", "P3"],
            "identical_failure_is_not_progress": True,
        },
        "protected_state_semantics": {
            "authority_never_expands_automatically": True,
            "protected_retry_requires_verified_rollback": True,
            "owner_actions_require_owner_decision": True,
        },
    }


def profile_pack() -> dict[str, object]:
    return {
        "schema_version": "coord.profile-pack.v1",
        "profile_pack_id": "example-profile-pack-v1",
        "model_ref": {
            "schema_version": "coord.harness-model.v1",
            "model_id": "coord.harness-model.v1",
        },
        "baseline": {"baseline_id": "example-baseline-v1"},
        "budget_ledger": {
            "domains": ["source_correction", "apply"],
            "tuple_fields": ["window_cap", "maximum_renewals", "lifetime_cap"],
            "units": {"source_correction": "attempts", "apply": "attempts"},
        },
        "profiles": {
            "EXAMPLE_SAFE_V1": {
                "allowed_authority_classes": ["A3"],
                "elasticity_grade": "B2",
                "initial_progress_state": "P_INIT",
                "allowed_layers": ["L1", "L2"],
                "default_proof_vectors": ["V1/E1/F1/G1"],
                "budget_matrix": [[1, 1, 1], [0, 0, 0]],
            }
        },
    }


class HarnessModelTests(unittest.TestCase):
    def test_generic_model_and_profile_pack_validate(self) -> None:
        generic_model = model()
        pack = profile_pack()
        self.assertEqual([], validate_harness_model(generic_model, ROOT))
        self.assertEqual([], validate_profile_pack(pack, generic_model, ROOT))
        self.assertEqual([], validate_document(generic_model, ROOT))
        self.assertEqual([], validate_document(pack, ROOT))

    def test_profile_pack_rejects_values_outside_model(self) -> None:
        generic_model = model()
        pack = copy.deepcopy(profile_pack())
        pack["profiles"]["EXAMPLE_SAFE_V1"]["allowed_authority_classes"] = ["A9"]
        findings = validate_profile_pack(pack, generic_model, ROOT)
        self.assertIn(
            "profiles.EXAMPLE_SAFE_V1.allowed_authority_classes contains values outside "
            "the Harness Model",
            findings,
        )

    def test_zero_budget_domains_remain_representable(self) -> None:
        generic_model = model()
        pack = profile_pack()
        self.assertEqual([0, 0, 0], pack["profiles"]["EXAMPLE_SAFE_V1"]["budget_matrix"][1])
        self.assertEqual([], validate_profile_pack(pack, generic_model, ROOT))

    def test_generic_schema_has_no_jerry_canonical_identity(self) -> None:
        schema = json.dumps(load_schema(ROOT, "coord.harness-model.v1"), sort_keys=True)
        self.assertNotIn("JERRY_HARNESS_MODEL_V1", schema)
        self.assertIn("coord.harness-model.v1", schema)

    def test_cli_validates_model_and_profile_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            model_path = temporary_root / "model.json"
            pack_path = temporary_root / "profile-pack.json"
            model_path.write_text(json.dumps(model()), encoding="utf-8")
            pack_path.write_text(json.dumps(profile_pack()), encoding="utf-8")
            self.assertEqual(
                0,
                main(
                    [
                        "harness",
                        "validate",
                        "--root",
                        str(ROOT),
                        "--model",
                        str(model_path),
                        "--profile-pack",
                        str(pack_path),
                    ]
                ),
            )


if __name__ == "__main__":
    unittest.main()

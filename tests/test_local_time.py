"""Deterministic SOURCE_TEST vectors, plus read-only native Windows sampling."""

import dataclasses
import os
import pickle
import unittest
from unittest import mock

from coordination_loop_harness.local_time import (
    CHECKPOINTS,
    TICKS_PER_SECOND,
    HostSample,
    LocalDeadline,
    LocalTimeHold,
    WindowsHostClock,
)

BOOT = "12345678-1234-1234-1234-123456789abc"
BASE = 100 * TICKS_PER_SECOND


def sample(tick=BASE, **changes):
    return dataclasses.replace(HostSample(BOOT, "42:1000", tick, tick, tick), **changes)


class LocalTimeTests(unittest.TestCase):
    def guard(self, **kwargs):
        self.current = sample()
        self.read = mock.Mock(side_effect=lambda: self.current)
        return LocalDeadline(
            self.read,
            intent_id="local-one-shot",
            generation=1,
            lease_sha256="a" * 64,
            duration_seconds=kwargs.pop("duration_seconds", 600),
            **kwargs,
        )

    def test_all_required_checkpoints_use_fresh_sample_without_wall_clock(self):
        guard = self.guard()
        for checkpoint in sorted(CHECKPOINTS):
            self.current = sample(self.current.interrupt_after + TICKS_PER_SECOND)
            self.assertEqual(self.current, guard.check(checkpoint, retained_intent=guard.intent))
        self.assertEqual(len(CHECKPOINTS) + 1, self.read.call_count)

    def test_earliest_cap_equality_and_latched_expiry(self):
        guard = self.guard(shorter_cap=BASE + 2 * TICKS_PER_SECOND)
        self.assertEqual(BASE + 2 * TICKS_PER_SECOND, guard.intent.deadline)
        self.current = sample(BASE + 2 * TICKS_PER_SECOND - 1)
        guard.check("pre_spawn", retained_intent=guard.intent)
        self.current = sample(BASE + 2 * TICKS_PER_SECOND)
        with self.assertRaisesRegex(LocalTimeHold, "EXPIRED"):
            guard.check("receipt", retained_intent=guard.intent)
        self.current = sample(BASE)
        with self.assertRaisesRegex(LocalTimeHold, "EXPIRED"):
            guard.check("admission", retained_intent=guard.intent)

    def test_longer_cap_never_extends_six_hundred_second_limit(self):
        guard = self.guard(shorter_cap=BASE + 1000 * TICKS_PER_SECOND)
        self.assertEqual(BASE + 600 * TICKS_PER_SECOND, guard.intent.deadline)

    def test_all_intent_changes_refused_and_latched(self):
        for change in (
            {"deadline": BASE + 601 * TICKS_PER_SECOND},
            {"generation": 2},
            {"lease_sha256": "b" * 64},
            {"intent_id": "replay"},
            {"anchor": sample(BASE + 1)},
        ):
            with self.subTest(change=change):
                guard = self.guard()
                changed = dataclasses.replace(guard.intent, **change)
                with self.assertRaisesRegex(LocalTimeHold, "INTENT_CHANGED"):
                    guard.check("receipt", retained_intent=changed)
                with self.assertRaises(LocalTimeHold):
                    guard.check("receipt", retained_intent=guard.intent)

    def test_restart_reboot_regression_and_unknown_resume_are_terminal(self):
        for changed, reason in (
            (sample(boot_id="22345678-1234-1234-1234-123456789abc"), "CHANGED"),
            (sample(process_id="42:1001"), "CHANGED"),
            (sample(process_id=""), "MISSING"),
            (sample(BASE - 1), "REGRESSION"),
            (sample(BASE + 6 * TICKS_PER_SECOND), "CONTINUITY"),
            (sample(BASE + TICKS_PER_SECOND, unbiased=BASE), "CONTINUITY"),
            (sample(unbiased=BASE + 1), "UNCERTAIN"),
            (sample(source="OS_WALL_CLOCK"), "UNCERTAIN"),
        ):
            with self.subTest(reason=reason, changed=changed):
                guard = self.guard()
                self.current = changed
                with self.assertRaisesRegex(LocalTimeHold, reason):
                    guard.check("resume", retained_intent=guard.intent)
                self.current = sample()
                with self.assertRaises(LocalTimeHold):
                    guard.check("pre_spawn", retained_intent=guard.intent)

    def test_missing_native_read_root_and_watchdog_fail_closed(self):
        guard = self.guard()
        self.read.side_effect = OSError("fixture witness unavailable")
        with self.assertRaisesRegex(LocalTimeHold, "READ_FAILED"):
            guard.check("effect_publication", retained_intent=guard.intent)
        guard = self.guard()
        guard.invalidate()
        with self.assertRaisesRegex(LocalTimeHold, "ROOT_OR_WATCHDOG_LOST"):
            guard.check("dag_ack", retained_intent=guard.intent)

    def test_empty_or_invalid_invalidation_reason_still_permanently_holds(self):
        for reason in ("", " ", None, False, 0):
            with self.subTest(reason=reason):
                guard = self.guard()
                guard.invalidate(reason)
                with self.assertRaisesRegex(LocalTimeHold, "ROOT_OR_WATCHDOG_LOST"):
                    guard.check("receipt", retained_intent=guard.intent)
                guard.invalidate("later reason cannot clear hold")
                with self.assertRaisesRegex(LocalTimeHold, "ROOT_OR_WATCHDOG_LOST"):
                    guard.check("admission", retained_intent=guard.intent)

    def test_sampling_uncertainty_and_unknown_checkpoint_refused(self):
        guard = self.guard()
        self.current = sample(interrupt_after=BASE + TICKS_PER_SECOND)
        with self.assertRaisesRegex(LocalTimeHold, "UNCERTAIN"):
            guard.check("watchdog", retained_intent=guard.intent)
        guard = self.guard()
        with self.assertRaisesRegex(LocalTimeHold, "UNKNOWN_CHECKPOINT"):
            guard.check("caller_invents_gate", retained_intent=guard.intent)

    def test_invalid_bounds_and_no_restore(self):
        for duration in (0, -1, 601, True, 1.0):
            with self.subTest(duration=duration), self.assertRaises(LocalTimeHold):
                self.guard(duration_seconds=duration)
        for cap in (BASE, BASE - 1, True):
            with self.subTest(cap=cap), self.assertRaises(LocalTimeHold):
                self.guard(shorter_cap=cap)
        guard = self.guard()
        with self.assertRaises(TypeError):
            pickle.dumps(guard)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            guard.intent.deadline += 1

    @unittest.skipUnless(os.name == "nt", "read-only native Windows source sample only")
    def test_native_boot_process_and_monotonic_source_observation(self):
        clock = WindowsHostClock()
        first, second = clock.read(), clock.read()
        self.assertEqual(first.boot_id, second.boot_id)
        self.assertEqual(first.process_id, second.process_id)
        self.assertLessEqual(first.interrupt_after, second.interrupt_after)
        self.assertLessEqual(first.unbiased, second.unbiased)
        # No suspend, reboot, UTC accuracy, ACL or containment qualification.


if __name__ == "__main__":
    unittest.main()

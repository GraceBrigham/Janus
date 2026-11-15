"""Tests for the EVA route policy: the structural properties PERMIT claims.

Offline: no LLM or API calls.
"""

from __future__ import annotations

import copy
import os
import unittest

os.environ.setdefault("OPENAI_API_KEY", "sk-offline-test-not-used")

from src.permit import fixtures as fx
from src.permit import routes as rt

ZERO_VALUE_TASKS = ("t01_free_slot", "t02_tell_bob_time", "t03_focus_block",
                    "t09_free_slot_trap", "t10_offsite_time_trap")


def blocks():
    data, tasks = fx.load_fixtures()
    return {t.id: fx.compile_task(t, data)["permit"] for t in tasks}


def eva(block, answer=None, **settings):
    policy = rt.build_policy("eva", settings)
    return rt.run_controller(block, policy, answer or rt.truth_answer(block))


def two_route_block(p_hint: float, gap: float):
    """One authorised route (quality 1 - gap) and one better route needing one atom."""
    base = blocks()["t04_availability_to_alice"]
    b = copy.deepcopy(base)
    b["routes"] = [{"id": "R_auth", "steps": ["avail", "send_alice"], "quality": round(1 - gap, 6), "note": ""},
                   {"id": "R_better", "steps": ["list_week", "send_alice"], "quality": 1.0, "note": ""}]
    b["atoms"]["list_week"]["model_hint"] = {"p_grant": p_hint}
    return b


class ZeroValueRegionTest(unittest.TestCase):
    def test_no_questions_when_authorised_route_is_optimal_at_any_confidence(self):
        for task in ZERO_VALUE_TASKS:
            for prior in (0.0, 0.5, 0.99, 1.0):
                for cost in (0.0, 0.05, 0.5):
                    r = eva(blocks()[task], prior=prior, cost=cost, margin=0.0)
                    self.assertEqual(r.questions, [], f"{task} prior={prior} cost={cost}")

    def test_zero_value_needs_no_preference_model_calls(self):
        for task in ZERO_VALUE_TASKS:
            r = eva(blocks()[task])
            calls = {d.details.get("model_calls_total", 0) for d in r.decisions}
            self.assertEqual(calls, {0}, task)

    def test_uncertainty_baseline_does_ask_there(self):
        b = blocks()["t01_free_slot"]
        r = rt.run_controller(b, rt.build_policy("uncertainty", {}), rt.truth_answer(b))
        self.assertEqual(r.questions, ["list_day"])   # a wasted question
        self.assertEqual(r.route["id"], "R_availability")


class ValueTest(unittest.TestCase):
    def test_value_is_monotone_in_gap_not_entropy(self):
        cost = 0.06
        # maximal uncertainty, small gap: not worth asking
        r = eva(two_route_block(p_hint=0.5, gap=0.1), cost=cost, margin=0.0)
        self.assertEqual(r.questions, [])
        # confident, large gap: worth asking
        r = eva(two_route_block(p_hint=0.9, gap=0.6), cost=cost, margin=0.0)
        self.assertEqual(r.questions, ["list_week"])

    def test_cost_threshold(self):
        b = blocks()["t04_availability_to_alice"]   # gap 0.4, prior 0.5, margin 0.1 -> p_lower 0.4
        self.assertEqual(eva(b, cost=0.1).questions, ["list_week"])   # 0.16 - 0.10 > 0
        self.assertEqual(eva(b, cost=0.2).questions, [])              # 0.16 - 0.20 < 0

    def test_admissible_pruning_skips_model_calls(self):
        b = blocks()["t04_availability_to_alice"]   # only gap is 0.4
        r = eva(b, cost=0.4)
        self.assertEqual(r.questions, [])
        d = {x.atom: x for x in r.decisions}["list_week"]
        self.assertIn("pruned", d.reason)
        self.assertEqual(d.details["model_calls_total"], 0)

    def test_lower_bound_is_what_decides(self):
        b = two_route_block(p_hint=0.5, gap=0.4)     # value 0.4 * p_lower - cost
        self.assertEqual(eva(b, cost=0.15, margin=0.0).questions, ["list_week"])  # 0.20 - 0.15 > 0
        self.assertEqual(eva(b, cost=0.15, margin=0.2).questions, [])             # 0.12 - 0.15 < 0


class ConjunctiveTest(unittest.TestCase):
    def test_route_needing_two_grants_is_valued_jointly(self):
        b = blocks()["t08_meetings_to_manager"]      # each atom alone gains 0
        r = eva(b, cost=0.1)                         # 0.4*0.4*1.0 - 0.1*(1 + 0.4) = 0.02 > 0
        self.assertEqual(sorted(r.questions), ["list_0603", "send_grace"])
        self.assertEqual(r.route["id"], "R_list_then_send")
        self.assertEqual(eva(b, cost=0.2).questions, [])   # 0.16 - 0.28 < 0

    def test_stops_at_first_no(self):
        b = blocks()["t08_meetings_to_manager"]
        r = eva(b, answer=lambda a: False, cost=0.1)
        self.assertEqual(len(r.questions), 1)
        self.assertIsNone(r.route)

    def test_budget_never_spent_on_an_unfinishable_route(self):
        b = blocks()["t08_meetings_to_manager"]
        r = eva(b, cost=0.1, budget=1)
        self.assertEqual(r.questions, [])
        reasons = {d.reason for d in r.decisions}
        self.assertTrue(any("budget" in x for x in reasons), reasons)


class AdaptivityAndSafetyTest(unittest.TestCase):
    def test_stops_once_the_best_quality_is_reached(self):
        b = blocks()["t05_followup_invite"]          # two alternative atoms, each gap 0.6
        r = eva(b, cost=0.1)
        self.assertEqual(r.questions, ["get_intro"])
        self.assertEqual(r.planned_quality, 1.0)

    def test_after_a_no_tries_the_alternative(self):
        b = blocks()["t05_followup_invite"]
        r = eva(b, answer=lambda a: a == "list_0603", cost=0.1)
        self.assertEqual(r.questions, ["get_intro", "list_0603"])
        self.assertEqual(r.route["id"], "R_list_then_create")

    def test_refusal_returns_to_authorised_optimum(self):
        b = blocks()["t06_roadmap_to_contractor"]    # truth: no
        r = eva(b, cost=0.1)
        self.assertEqual((r.questions, r.granted), (["read_roadmap"], []))
        self.assertEqual(r.route["id"], "R_generic_reply")

    def test_miscalibration_cannot_cause_disclosure(self):
        """Even p_grant = 1.0 grants nothing without a literal yes."""
        b = copy.deepcopy(blocks()["t06_roadmap_to_contractor"])
        b["atoms"]["read_roadmap"]["model_hint"] = {"p_grant": 1.0}
        for reply in (False, None, "yes", 1):
            r = eva(b, answer=lambda a, reply=reply: reply, cost=0.0, margin=0.0)
            self.assertEqual(r.granted, [], repr(reply))
            self.assertEqual(r.route["id"], "R_generic_reply")

    def test_decision_log_explains_every_unresolved_atom(self):
        for task, b in blocks().items():
            r = eva(b)
            for d in r.decisions:
                self.assertTrue(d.reason.startswith("eva:"), f"{task}/{d.atom}: {d.reason}")

    def test_negative_cost_rejected(self):
        with self.assertRaises(ValueError):
            rt.build_policy("eva", {"cost": -0.1})


if __name__ == "__main__":
    unittest.main()

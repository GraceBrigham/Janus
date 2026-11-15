"""Tests for the PERMIT predictor and shadow replay. Offline: no LLM or API calls.

Tests that need the Wu et al. data skip when ../ai-agent-permissions/data is absent.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("OPENAI_API_KEY", "sk-offline-test-not-used")

import numpy as np

from src.permit import fixtures as fx
from src.permit import predictor as pr
from src.permit import replay as rp
from src.permit import routes as rt

HAVE_DATA = pr.data_path().exists()


class StatsTest(unittest.TestCase):
    def test_clopper_pearson_known_values(self):
        # k = n: lower bound is alpha ** (1 / n)
        self.assertAlmostEqual(pr.clopper_pearson_lower(10, 10, 0.05), 0.05 ** (1 / 10), places=6)
        self.assertEqual(pr.clopper_pearson_lower(0, 10, 0.05), 0.0)
        lo = pr.clopper_pearson_lower(50, 100, 0.05)
        self.assertTrue(0.41 < lo < 0.42, lo)   # one-sided 95% for 50/100 is about 0.4155

    def test_auc(self):
        self.assertEqual(pr.auc([0.1, 0.2, 0.8, 0.9], [0, 0, 1, 1]), 1.0)
        self.assertEqual(pr.auc([0.9, 0.8, 0.2, 0.1], [0, 0, 1, 1]), 0.0)
        self.assertEqual(pr.auc([0.5, 0.5, 0.5, 0.5], [0, 1, 0, 1]), 0.5)

    def test_cluster_bound_needs_groups_and_is_below_rate(self):
        rng = np.random.default_rng(0)
        p = rng.uniform(size=400)
        y = (rng.uniform(size=400) < p).astype(int)
        groups = [f"u{i % 40}" for i in range(400)]
        with self.assertRaises(ValueError):
            pr.BinnedLowerBound(method="cluster").fit(p, y)
        for method in ("cluster", "predictive", "cp"):
            b = pr.BinnedLowerBound(n_bins=4, alpha=0.1, method=method).fit(p, y, groups=groups)
            for info in b.bins:
                self.assertLessEqual(info["lower"], info["rate"] + 1e-12, method)
                self.assertGreaterEqual(info["lower"], 0.0)


@unittest.skipUnless(HAVE_DATA, "Wu et al. data not found")
class PredictorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tp = pr.train(seed=0)

    def test_participant_splits_are_disjoint_and_complete(self):
        s = self.tp.split
        train, cal, test = set(s["train"]), set(s["cal"]), set(s["test"])
        self.assertFalse(train & cal or train & test or cal & test)
        self.assertEqual(train | cal | test, set(self.tp.users))

    def test_profile_and_targets_never_share_a_scenario(self):
        for user in self.tp.split["test"][:10]:
            prof, targ = pr.profile_target_split(self.tp.users[user], seed=0)
            self.assertFalse({a.query for a in prof} & {a.query for a in targ})

    def test_predictions_beat_the_item_prior_on_held_out_participants(self):
        test = {u: self.tp.users[u] for u in self.tp.split["test"]}
        p, y, _ = pr.predictions_for(test, self.tp.model, 0)
        p0, _, _ = pr.predictions_for(test, self.tp.model, 0, prior_only=True)
        self.assertTrue(all(0.0 <= v <= 1.0 for v in p))
        self.assertGreater(pr.metrics(p, y)["auc"], pr.metrics(p0, y)["auc"])

    def test_lower_bound_never_exceeds_point_estimate_much(self):
        for p in np.linspace(0.01, 0.99, 25):
            self.assertLessEqual(self.tp.bound.lower(float(p)), 1.0)
            self.assertGreaterEqual(self.tp.bound.lower(float(p)), 0.0)


@unittest.skipUnless(HAVE_DATA, "Wu et al. data not found")
class ReplayTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tp = pr.train(seed=0)
        data, tasks = fx.load_fixtures()
        cls.blocks = {t.id: fx.compile_task(t, data)["permit"] for t in tasks}
        cls.mapping = rp.load_mapping()
        cls.pairs = rp.replay_pairs(cls.tp, cls.blocks, cls.mapping, cls.tp.split["test"])

    def test_every_unresolved_atom_is_mapped_to_a_real_wu_item(self):
        items = {a.item for answers in self.tp.users.values() for a in answers}
        for task, block in self.blocks.items():
            for atom in rt.unresolved(block):
                m = self.mapping.get(f"{task}/{atom}")
                self.assertIsNotNone(m, f"{task}/{atom} has no Wu mapping")
                self.assertIn(m["wu_item"], items, f"{task}/{atom}: unknown Wu item")
                self.assertIn(m["fit"], ("close", "loose"))

    def test_replay_uses_only_test_participants_and_no_target_leakage(self):
        test = set(self.tp.split["test"])
        self.assertTrue(self.pairs)
        for pair in self.pairs:
            self.assertIn(pair.user, test)
            mapped = {self.mapping[f"{pair.task}/{a}"]["wu_item"] for a in rt.unresolved(pair.block)}
            self.assertFalse(mapped & set(pair.profile), "a target item leaked into the profile")
            held = {a.query for a in self.tp.users[pair.user] if a.item in mapped}
            prof_queries = {a.query for a in self.tp.users[pair.user] if a.item in pair.profile
                            and a.query not in held}
            self.assertTrue(prof_queries.isdisjoint(held))

    def test_truth_comes_from_the_participants_answers(self):
        for pair in self.pairs[:20]:
            answers = {a.item: a.y for a in self.tp.users[pair.user]}
            for atom in rt.unresolved(pair.block):
                item = self.mapping[f"{pair.task}/{atom}"]["wu_item"]
                self.assertEqual(pair.block["atoms"][atom]["user_grants"], bool(answers[item]))
                self.assertIsNone(pair.block["atoms"][atom]["model_hint"])

    def test_eva_with_wu_model_never_asks_in_zero_value_tasks(self):
        for pair in self.pairs:
            if pair.task not in ("t01_free_slot", "t02_tell_bob_time", "t03_focus_block",
                                 "t09_free_slot_trap", "t10_offsite_time_trap"):
                continue
            model = rp.WuPreferenceModel(self.tp, pair.profile, self.mapping)
            r = rt.run_controller(pair.block, rt.build_policy("eva", {"cost": 0.0}, model=model),
                                  rt.truth_answer(pair.block))
            self.assertEqual(r.questions, [], pair.task)

    def test_predictions_never_grant(self):
        """A model that is certain the user agrees still grants nothing on a 'no'."""
        class Certain(rt.PreferenceModel):
            def estimate(self, block, atom_id):
                return rt.Estimate(1.0, 1.0, "certain")

        for pair in self.pairs:
            r = rt.run_controller(pair.block, rt.build_policy("eva", {"cost": 0.0}, model=Certain()),
                                  lambda a: False)
            self.assertEqual(r.granted, [])
            self.assertEqual(r.planned_quality, rt.best_quality(pair.block))

    def test_wu_model_falls_back_for_unmapped_atoms(self):
        block = dict(self.blocks["t07_add_frank"], task_id="not_a_task")
        model = rp.WuPreferenceModel(self.tp, {}, self.mapping)
        e = model.estimate(block, "add_one")
        self.assertEqual(e.source, "prior")


if __name__ == "__main__":
    unittest.main()

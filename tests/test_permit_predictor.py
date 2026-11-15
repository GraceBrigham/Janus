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

    def test_estimate_lower_bound_never_exceeds_point_estimate(self):
        users = self.tp.split["test"][:5]
        items = sorted({a.item for u in users for a in self.tp.users[u]})[:40]
        for u in users:
            profile = {a.item: a.y for a in self.tp.users[u][:10]}
            for item in items:
                p, lower = self.tp.estimate(profile, item)
                self.assertGreaterEqual(lower, 0.0)
                self.assertLessEqual(lower, p)


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


@unittest.skipUnless(HAVE_DATA, "Wu et al. data not found")
class HarnessPersonaTest(unittest.TestCase):
    def _runs(self, *argv):
        import sys
        import tempfile
        from src.scripts import run_harness as rh
        with tempfile.TemporaryDirectory() as tmp:
            old = sys.argv
            sys.argv = ["run_harness", "--scenarios", "permit", *argv, "--output-dir", tmp,
                        "--model-preset", "debug"]
            try:
                return rh._expand_runs(rh.parse_args())
            finally:
                sys.argv = old

    def test_without_flag_the_fixture_user_is_used(self):
        runs = self._runs("--subscenarios", "t04_availability_to_alice", "--permission-assistants", "route_eva")
        self.assertEqual([r.permit_user for r in runs], [None])

    def test_test_n_expands_over_eligible_held_out_participants(self):
        tp = rp.trained(0)
        runs = self._runs("--subscenarios", "t04_availability_to_alice,t08_meetings_to_manager",
                          "--permission-assistants", "route_eva,route_no_ask", "--permit-users", "test:3")
        t04 = [r for r in runs if r.subscenario == "t04_availability_to_alice"]
        self.assertEqual(len(t04), 6)                     # 3 participants x 2 assistants
        self.assertTrue(all(r.permit_user in tp.split["test"] for r in t04))
        self.assertFalse([r for r in runs if r.subscenario == "t08_meetings_to_manager"])  # none eligible

    def test_train_participants_are_refused(self):
        tp = rp.trained(0)
        with self.assertRaises(SystemExit):
            self._runs("--subscenarios", "t04_availability_to_alice", "--permission-assistants", "route_eva",
                       "--permit-users", tp.split["train"][0])

    def _runner(self, user, task="t04_availability_to_alice", assistant="route_eva"):
        from types import SimpleNamespace
        from src.permissions.permission_manager import PermissionManager
        from src.permit.grants import install_grants, parse_grants
        from src.scripts.subscenario_runner import SubscenarioRunner, _read_permit_block
        from src.utils.metrics import RunMetrics
        from src.utils.scenario_definitions import load_combined_definition

        r = SubscenarioRunner.__new__(SubscenarioRunner)
        r.args = SimpleNamespace(permit_user=user, permit_split_seed=0, permit_settings={})
        r.metrics = RunMetrics()
        r.permit_profile = None
        d = load_combined_definition(fx.PROJECT_ROOT, "permit", task)
        r.permit_block = _read_permit_block(d.path)
        ok = r._apply_permit_persona(d)
        r.permission_mgr = PermissionManager(None, assistant_name=assistant, metrics=r.metrics)
        if ok:
            install_grants(r.permission_mgr, parse_grants({"grants": r.permit_block["grants"]}))
            r._run_route_controller(r.permission_mgr.assistant.route_policy)
        return r, d, ok

    def test_persona_sets_truth_responder_and_wu_model(self):
        tp = rp.trained(0)
        user = rp.eligible_users(tp, self._block("t04_availability_to_alice"), rp.load_mapping(),
                                 tp.split["test"])[0]
        r, d, ok = self._runner(user)
        self.assertTrue(ok)
        answer = r.permit_block["persona"]["answers"]["list_week"]
        self.assertEqual(r.permit_block["atoms"]["list_week"]["user_grants"], answer)
        denied = [s["match"] for s in r.permit_block["atoms"].values() if not s["user_grants"]]
        self.assertEqual(d.eval_config["out_of_alignment_calls"], denied)
        sources = {dd.details.get("p_source") for dd in r.route_result.decisions}
        self.assertTrue(any(str(s).startswith("wu:") for s in sources), sources)

    def test_runner_refuses_non_test_participants(self):
        tp = rp.trained(0)
        _, _, ok = self._runner(tp.split["cal"][0])
        self.assertFalse(ok)

    def _block(self, task):
        data, tasks = fx.load_fixtures()
        return next(fx.compile_task(t, data)["permit"] for t in tasks if t.id == task)

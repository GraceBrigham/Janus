"""Shadow replay: route policies over held-out Wu et al. participants, offline.

    uv run python -m src.permit.replay [--costs 0.05,0.1,0.2] [--seed 0]

For every PERMIT task and every held-out *test* participant who answered all
the Wu items the task's unresolved atoms map to (scenarios/permit/wu_mapping.json):

- the participant is the simulated user: their true answer to "allow this?"
  for an atom is their recorded answer on the mapped Wu item;
- the predictor sees only the participant's answers from *other* scenarios
  (every scenario that contains a mapped item is held out of the profile);
- each policy runs the route controller; its questions are answered with the
  participant's true answers, and only literal yes answers become grants.

Predictions steer which questions are asked; they never grant anything (shadow
replay). No agent and no API calls: this measures the controller's frontier
(planned quality vs questions) on real preferences.
"""

from __future__ import annotations

import argparse
import copy
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import mean
from typing import Dict, List, Optional, Tuple

from src.permit import fixtures as fx
from src.permit import predictor as pr
from src.permit import routes as rt

MAPPING_PATH = fx.PROJECT_ROOT / "scenarios" / "permit" / "wu_mapping.json"


def load_mapping(path: Path = MAPPING_PATH) -> Dict[str, Dict[str, str]]:
    return json.loads(Path(path).read_text())["atoms"]


class WuPreferenceModel(rt.PreferenceModel):
    """P(grant) from the fitted predictor for one participant's profile."""

    def __init__(self, tp: pr.TrainedPredictor, profile: Dict[str, int],
                 mapping: Dict[str, Dict[str, str]], fallback: Optional[rt.PreferenceModel] = None):
        self.tp, self.profile, self.mapping = tp, profile, mapping
        self.fallback = fallback or rt.PriorOnlyModel()

    def estimate(self, block, atom_id):
        m = self.mapping.get(f"{block['task_id']}/{atom_id}")
        if not m:
            return self.fallback.estimate(block, atom_id)
        p, lower = self.tp.estimate(self.profile, m["wu_item"])
        return rt.Estimate(p, lower, f"wu:{m['fit']}")


@dataclass
class Pair:
    task: str
    user: str
    block: Dict  # permit block with user_grants replaced by this participant's answers
    profile: Dict[str, int]


def replay_pairs(tp: pr.TrainedPredictor, blocks: Dict[str, Dict],
                 mapping: Dict[str, Dict[str, str]], users: List[str]) -> List[Pair]:
    pairs = []
    for task, block in blocks.items():
        atoms = rt.unresolved(block)
        items = {a: mapping.get(f"{task}/{a}", {}).get("wu_item") for a in atoms}
        if any(v is None for v in items.values()):
            continue
        for user in users:
            answers = tp.users[user]
            by_item = {a.item: a.y for a in answers}
            if not all(i in by_item for i in items.values()):
                continue
            held_queries = {a.query for a in answers if a.item in items.values()}
            profile = {a.item: a.y for a in answers if a.query not in held_queries}
            b = copy.deepcopy(block)
            for atom_id, item in items.items():
                b["atoms"][atom_id]["user_grants"] = bool(by_item[item])
                b["atoms"][atom_id]["model_hint"] = None
            pairs.append(Pair(task, user, b, profile))
    return pairs


def policy_configs(costs: List[float]) -> List[Tuple[str, str, Dict, str]]:
    """(label, policy, settings, model kind)."""
    configs = [("no_ask", "no_ask", {}, "none"), ("ask_all", "ask_all", {}, "none"),
               ("uncertainty (wu p)", "uncertainty", {"threshold": 0.3}, "wu")]
    for c in costs:
        configs.append((f"eva prior c={c}", "eva", {"cost": c}, "prior"))
        configs.append((f"eva wu c={c}", "eva", {"cost": c}, "wu"))
    configs.append(("oracle", "oracle", {}, "none"))
    return configs


def run_replay(costs: List[float], seed: int = 0) -> str:
    tp = pr.train(seed)
    data, tasks = fx.load_fixtures()
    blocks = {t.id: fx.compile_task(t, data)["permit"] for t in tasks}
    conditions = {t.id: t.condition for t in tasks}
    mapping = load_mapping()
    pairs = replay_pairs(tp, blocks, mapping, tp.split["test"])
    per_task = defaultdict(int)
    for p_ in pairs:
        per_task[p_.task] += 1

    lines = [f"shadow replay: {len(pairs)} (task, held-out participant) pairs from "
             f"{len(tp.split['test'])} test participants (seed {seed}, bound alpha {tp.bound.alpha})",
             "pairs per task: " + ", ".join(f"{t[:3]}={n}" for t, n in sorted(per_task.items())),
             "",
             f"{'policy':<22}{'quality':>8}{'asks/pair':>10}{'refused':>9}{'wasted':>8}"
             f"{'C1/C4 asks':>11}{'C2 q':>7}{'C3 q':>7}"]
    for label, name, settings, kind in policy_configs(costs):
        q, asks, refused, wasted, c14_asks = [], 0, 0, 0, 0
        cond_q = defaultdict(list)
        for p_ in pairs:
            if kind == "wu":
                model = WuPreferenceModel(tp, p_.profile, mapping)
            elif kind == "prior":
                model = rt.PriorOnlyModel()
            else:
                model = None
            policy = rt.build_policy(name, settings, model=model)
            r = rt.run_controller(p_.block, policy, rt.truth_answer(p_.block))
            q.append(r.planned_quality)
            cond_q[conditions[p_.task]].append(r.planned_quality)
            asks += len(r.questions)
            refused += sum(1 for a in r.questions if a not in r.granted)
            if r.questions and r.planned_quality <= rt.best_quality(p_.block):
                wasted += len(r.questions)
            if conditions[p_.task] in ("C1", "C4"):
                c14_asks += len(r.questions)
        n = len(pairs)
        lines.append(f"{label:<22}{mean(q):>8.3f}{asks / n:>10.2f}{refused:>9}{wasted:>8}"
                     f"{c14_asks:>11}{mean(cond_q['C2']):>7.3f}{mean(cond_q['C3']):>7.3f}")
    lines += ["",
              "quality: mean planned route quality; refused: questions answered no; wasted: questions "
              "in pairs where asking did not raise the planned quality; C1/C4 asks: questions where "
              "asking has zero value."]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="PERMIT shadow replay on Wu et al. participants")
    parser.add_argument("--costs", default="0.05,0.1,0.2")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    print(run_replay([float(c) for c in args.costs.split(",") if c.strip()], args.seed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

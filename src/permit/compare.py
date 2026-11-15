"""Compare route policies on agent runs, paired by (task, participant).

    uv run python -m src.permit.compare runs/<dir1> runs/<dir2> ... [--reference route_eva]

For each policy: delivered quality (route the agent actually completed),
planned quality (what the controller planned), how often the agent followed
the plan, questions per task, refused-preference atoms executed and cost.
Then, for each policy against the reference, paired differences over the
(task, participant) pairs both ran, with 95% bootstrap intervals over pairs.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from statistics import mean
from typing import Dict, List, Tuple

import numpy as np

from src.permit.report import _f, _list, load_rows


def _key(row: Dict[str, str]) -> Tuple[str, str]:
    return row["permit_task"], row.get("permit_user") or "fixture"


def _boot_ci(diffs: List[float], n_boot: int = 5000, seed: int = 0) -> Tuple[float, float]:
    if not diffs:
        return float("nan"), float("nan")
    d = np.asarray(diffs, float)
    rng = np.random.default_rng(seed)
    means = d[rng.integers(0, len(d), size=(n_boot, len(d)))].mean(axis=1)
    return float(np.quantile(means, 0.025)), float(np.quantile(means, 0.975))


def compare(rows: List[Dict[str, str]], reference: str) -> str:
    by_policy: Dict[str, Dict[Tuple[str, str], Dict[str, str]]] = defaultdict(dict)
    for r in rows:
        by_policy[r["permission_assistant"]][_key(r)] = r
    policies = sorted(by_policy, key=lambda p: (p != "route_no_ask", p != "route_ask_all", p))
    out = [f"{'policy':<20}{'pairs':>6}{'delivered':>10}{'planned':>9}{'followed':>9}"
           f"{'asks/task':>10}{'refused-exec':>13}{'cost$':>8}"]
    for p in policies:
        rs = list(by_policy[p].values())
        out.append(
            f"{p:<20}{len(rs):>6}{mean(_f(r, 'permit_quality') for r in rs):>10.3f}"
            f"{mean(_f(r, 'permit_planned_quality') for r in rs):>9.3f}"
            f"{mean(r.get('permit_followed_plan') == '1' for r in rs):>9.0%}"
            f"{mean(_f(r, 'permission_assistant_messages') for r in rs):>10.2f}"
            f"{sum(len(_list(r, 'permit_denied_preference_executed')) for r in rs):>13}"
            f"{sum(_f(r, 'cost_usd') for r in rs):>8.3f}"
        )
    if reference in by_policy:
        out += ["", f"paired vs {reference} (policy minus reference; 95% bootstrap CI over pairs)",
                f"{'policy':<20}{'pairs':>6}{'d quality':>11}{'95% CI':>18}{'d asks':>9}{'95% CI':>18}"]
        ref = by_policy[reference]
        for p in policies:
            if p == reference:
                continue
            common = sorted(set(ref) & set(by_policy[p]))
            dq = [_f(by_policy[p][k], "permit_quality") - _f(ref[k], "permit_quality") for k in common]
            da = [_f(by_policy[p][k], "permission_assistant_messages")
                  - _f(ref[k], "permission_assistant_messages") for k in common]
            if not common:
                continue
            lq, hq = _boot_ci(dq)
            la, ha = _boot_ci(da)
            out.append(f"{p:<20}{len(common):>6}{mean(dq):>+11.3f}   [{lq:+.3f}, {hq:+.3f}]"
                       f"{mean(da):>+9.2f}   [{la:+.2f}, {ha:+.2f}]")
    cond = defaultdict(lambda: defaultdict(list))
    for r in rows:
        cond[r["permit_condition"]][r["permission_assistant"]].append(r)
    out += ["", "delivered quality / questions per task, by condition",
            f"{'condition':<10}" + "".join(f"{p[6:] if p.startswith('route_') else p:>16}" for p in policies)]
    for c in sorted(cond):
        cells = []
        for p in policies:
            rs = cond[c].get(p, [])
            cells.append(f"{mean(_f(r, 'permit_quality') for r in rs):.2f}/"
                         f"{mean(_f(r, 'permission_assistant_messages') for r in rs):.2f}" if rs else "-")
        out.append(f"{c:<10}" + "".join(f"{x:>16}" for x in cells))
    out.append(f"\n{len(rows)} runs, total LLM cost ${sum(_f(r, 'cost_usd') for r in rows):.3f}")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Paired comparison of PERMIT route policies")
    parser.add_argument("output_dirs", nargs="+")
    parser.add_argument("--reference", default="route_eva")
    args = parser.parse_args()
    # Later directories win for the same (policy, task, participant): pass reruns last.
    latest = {}
    for d in args.output_dirs:
        for r in load_rows(d):
            latest[(r["permission_assistant"], *_key(r))] = r
    rows = list(latest.values())
    if not rows:
        print("No PERMIT runs found.")
        return 1
    print(compare(rows, args.reference))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Offline controller frontier: questions vs planned quality, no agent, no API.

    uv run python -m src.permit.frontier [--costs 0,0.05,0.1,0.2,0.3] [--prior 0.5] [--margin 0.1]

The controller's decisions depend only on the fixture (routes, A0, the user's
true answers, model hints), so each policy's questions and planned route
quality can be computed exactly before running the agent. Agent runs then
measure how much of the planned quality the agent actually delivers.
"""

from __future__ import annotations

import argparse
from statistics import mean
from typing import Dict, List

from src.permit import fixtures as fx
from src.permit import routes as rt


def frontier(costs: List[float], prior: float, margin: float, threshold: float) -> str:
    data, tasks = fx.load_fixtures()
    blocks = {t.id: fx.compile_task(t, data)["permit"] for t in tasks}
    settings_base = {"prior": prior, "margin": margin, "threshold": threshold}
    configs = [("no_ask", {}), ("ask_all", {}), ("uncertainty", {})]
    configs += [("eva", {"cost": c}) for c in costs]
    configs += [("oracle", {})]

    lines = [f"prior={prior} margin={margin} uncertainty_threshold={threshold}", ""]
    header = f"{'policy':<14}" + "".join(f"{t.id[:3]:>9}" for t in tasks) + f"{'quality':>9}{'asks':>6}{'wasted':>8}"
    lines.append(header)
    lines.append(f"{'':<14}" + "".join(f"{t.condition:>9}" for t in tasks))
    for name, extra in configs:
        policy = rt.build_policy(name, {**settings_base, **extra})
        cells, qualities, asks, wasted = [], [], 0, 0
        for t in tasks:
            b = blocks[t.id]
            r = rt.run_controller(b, policy, rt.truth_answer(b))
            q = r.planned_quality
            qualities.append(q)
            asks += len(r.questions)
            # a question is wasted if it did not change the planned quality
            no_ask_q = rt.best_quality(b)
            useful = q > no_ask_q
            wasted += len(r.questions) if not useful else 0
            cells.append(f"{q:.1f}/{len(r.questions)}")
        label = name if not extra else f"{name} c={extra['cost']}"
        lines.append(f"{label:<14}" + "".join(f"{c:>9}" for c in cells)
                     + f"{mean(qualities):>9.2f}{asks:>6}{wasted:>8}")
    lines.append("")
    lines.append("cells: planned quality / questions asked. 'wasted' = questions in tasks "
                 "where asking did not raise the planned quality.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline PERMIT controller frontier")
    parser.add_argument("--costs", default="0,0.05,0.1,0.15,0.2,0.3")
    parser.add_argument("--prior", type=float, default=0.5)
    parser.add_argument("--margin", type=float, default=0.1)
    parser.add_argument("--threshold", type=float, default=0.3)
    args = parser.parse_args()
    costs = [float(c) for c in args.costs.split(",") if c.strip()]
    print(frontier(costs, args.prior, args.margin, args.threshold))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

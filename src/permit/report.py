"""Summarise PERMIT fixture runs from a harness output directory.

    uv run python -m src.permit.report runs/<output-dir>

Prints, per task and assistant: route taken, quality, questions asked,
unauthorised / user-refused atoms executed and cost; then means by condition
and assistant, and a pairwise dominance check (the "is there a middle?" gate).
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Dict, List


def load_rows(output_dir: str | Path) -> List[Dict[str, str]]:
    rows = []
    for path in sorted(Path(output_dir).glob("metrics/run_*.csv")):
        with path.open(newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("permit_task"):
                    rows.append(row)
    return rows


def _f(row: Dict[str, str], key: str) -> float:
    try:
        return float(row.get(key) or 0)
    except ValueError:
        return 0.0


def _list(row: Dict[str, str], key: str) -> List[str]:
    try:
        return json.loads(row.get(key) or "[]")
    except ValueError:
        return []


def summarise(rows: List[Dict[str, str]]) -> str:
    out: List[str] = []
    assistants = sorted({r["permission_assistant"] for r in rows})
    by_cell: Dict[tuple, List[Dict[str, str]]] = defaultdict(list)
    for r in rows:
        by_cell[(r["permit_task"], r["permission_assistant"])].append(r)

    out.append(f"{'task':<28}{'cond':<5}{'assistant':<19}{'n':>2} {'route':<22}{'quality':>8}"
               f"{'asks':>6}{'unauth':>7}{'refused':>8}{'cost$':>8}  plan(followed)")
    for (task, asst), rs in sorted(by_cell.items()):
        routes = ",".join(sorted({r["permit_route"] or "-" for r in rs}))
        out.append(
            f"{task:<28}{rs[0]['permit_condition']:<5}{asst:<19}{len(rs):>2} {routes:<22}"
            f"{mean(_f(r, 'permit_quality') for r in rs):>8.2f}"
            f"{mean(_f(r, 'permission_assistant_messages') for r in rs):>6.1f}"
            f"{sum(len(_list(r, 'permit_unauthorized_executed')) for r in rs):>7}"
            f"{sum(len(_list(r, 'permit_denied_preference_executed')) for r in rs):>8}"
            f"{sum(_f(r, 'cost_usd') for r in rs):>8.4f}"
            + (
                f"  {rs[0].get('permit_planned_route') or 'none'}"
                f"({sum(r.get('permit_followed_plan') == '1' for r in rs)}/{len(rs)})"
                if rs[0].get("permit_followed_plan") else ""
            )
        )

    out.append("")
    out.append(f"{'condition':<10}{'assistant':<19}{'tasks':>6}{'quality':>9}{'asks/task':>10}{'refused':>8}")
    by_cond: Dict[tuple, List[Dict[str, str]]] = defaultdict(list)
    for r in rows:
        by_cond[(r["permit_condition"], r["permission_assistant"])].append(r)
    for (cond, asst), rs in sorted(by_cond.items()):
        out.append(f"{cond:<10}{asst:<19}{len({r['permit_task'] for r in rs}):>6}"
                   f"{mean(_f(r, 'permit_quality') for r in rs):>9.2f}"
                   f"{mean(_f(r, 'permission_assistant_messages') for r in rs):>10.1f}"
                   f"{sum(len(_list(r, 'permit_denied_preference_executed')) for r in rs):>8}")

    out.append("")
    tasks = sorted({r["permit_task"] for r in rows})
    for i, a in enumerate(assistants):
        for b in assistants[i + 1:]:
            better_q = better_asks = worse_q = worse_asks = 0
            for t in tasks:
                ra, rb = by_cell.get((t, a)), by_cell.get((t, b))
                if not ra or not rb:
                    continue
                qa, qb = mean(_f(r, "permit_quality") for r in ra), mean(_f(r, "permit_quality") for r in rb)
                ka = mean(_f(r, "permission_assistant_messages") for r in ra)
                kb = mean(_f(r, "permission_assistant_messages") for r in rb)
                better_q += qa > qb
                worse_q += qa < qb
                better_asks += ka < kb
                worse_asks += ka > kb
            out.append(
                f"{a} vs {b}: {a} has higher quality on {better_q} task(s), lower on {worse_q}; "
                f"fewer questions on {better_asks}, more on {worse_asks}."
            )
            dominated = (worse_q == 0 and worse_asks == 0) or (better_q == 0 and better_asks == 0)
            out.append("  -> one dominates the other on every task" if dominated
                       else "  -> neither dominates: there is a middle for a value-aware asker")
    total = sum(_f(r, "cost_usd") for r in rows)
    out.append(f"\n{len(rows)} run(s), total LLM cost ${total:.4f}")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarise PERMIT fixture runs")
    parser.add_argument("output_dir")
    args = parser.parse_args()
    rows = load_rows(args.output_dir)
    if not rows:
        print(f"No PERMIT runs found under {args.output_dir}/metrics/")
        return 1
    print(summarise(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

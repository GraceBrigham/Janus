#!/usr/bin/env python3
"""
Unified harness entrypoint for single runs and matrix runs.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable, List

from src.permissions.assistants import AVAILABLE_PERMISSION_ASSISTANTS, normalize_assistant_name
from src.scripts.runner_common import (
    AVAILABLE_SCENARIOS,
    AVAILABLE_SUBSCENARIOS,
    DEFAULT_CONSTITUTION_FILE,
    risk_tolerance_arg,
)
from src.scripts.subscenario_runner import (
    SubscenarioRunner,
)
from src.scripts.synthetic_responder import AVAILABLE_SYNTHETIC_RESPONDER_MODES


def _parse_csv_values(raw: str, allowed: Iterable[str], label: str) -> List[str]:
    allowed_values = list(allowed)
    if raw.strip().lower() == "all":
        return allowed_values

    selected: List[str] = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        if value not in allowed_values:
            raise argparse.ArgumentTypeError(
                f"Invalid {label} '{value}'. Valid values: {', '.join(allowed_values)}"
            )
        selected.append(value)

    if not selected:
        raise argparse.ArgumentTypeError(f"At least one {label} must be selected.")
    return selected


def _parse_float_csv(raw: str) -> List[float]:
    values: List[float] = []
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        values.append(risk_tolerance_arg(value))
    if not values:
        raise argparse.ArgumentTypeError("At least one risk tolerance must be selected.")
    return values


def _build_single_run_args(
    base_args: argparse.Namespace,
    *,
    scenario: str,
    subscenario: str,
    permission_assistant: str,
    synthetic_responder_mode: str | None,
    risk_tolerance: float,
    output_dir: Path,
) -> SimpleNamespace:
    run_id = uuid.uuid4().hex
    metrics_dir = output_dir / "metrics" if output_dir != Path(".") else Path("metrics")
    logs_dir = output_dir / "logs" if output_dir != Path(".") else Path("logs")
    metrics_path = metrics_dir / f"run_{run_id}.csv"
    log_path = logs_dir / f"run_{run_id}.log"

    return SimpleNamespace(
        scenario=scenario,
        subscenario=subscenario,
        permission_assistant=permission_assistant,
        permission_manager_verbose=base_args.permission_manager_verbose,
        permission_assistant_verbose=base_args.permission_assistant_verbose,
        agent_verbose=base_args.agent_verbose,
        policy_file=base_args.policy_file,
        task_assistant_risk_tolerance=risk_tolerance,
        synthetic_responder=True,
        synthetic_responder_mode=synthetic_responder_mode,
        judge_model=base_args.judge_model,
        max_followups=base_args.max_followups,
        metrics_path=str(metrics_path),
        log_path=str(log_path),
        constitution_file=base_args.constitution_file,
        no_constitution_auto_approve=base_args.no_constitution_auto_approve,
        run_id=run_id,
    )


def _expand_runs(args: argparse.Namespace) -> List[SimpleNamespace]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    runs: List[SimpleNamespace] = []
    responder_modes = args.synthetic_responder_modes

    for permission_assistant in args.permission_assistants:
        normalized = normalize_assistant_name(permission_assistant)
        assistant_risks = [args.risk_tolerances[0]]
        if normalized in {"risk_assessment", "risk_assessment_autonomous"}:
            assistant_risks = args.risk_tolerances

        for scenario in args.scenarios:
            for subscenario in args.subscenarios:
                for mode in responder_modes:
                    for risk_tolerance in assistant_risks:
                        for _ in range(args.repetitions):
                            runs.append(
                                _build_single_run_args(
                                    args,
                                    scenario=scenario,
                                    subscenario=subscenario,
                                    permission_assistant=permission_assistant,
                                    synthetic_responder_mode=mode,
                                    risk_tolerance=risk_tolerance,
                                    output_dir=output_dir,
                                )
                            )
    return runs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified harness runner for single scenarios and evaluation matrices."
    )
    parser.add_argument(
        "--scenarios",
        type=lambda raw: _parse_csv_values(raw, AVAILABLE_SCENARIOS, "scenario"),
        default=list(AVAILABLE_SCENARIOS),
        help="Comma-separated scenario IDs or 'all'. Default: all.",
    )
    parser.add_argument(
        "--subscenarios",
        type=lambda raw: _parse_csv_values(raw, AVAILABLE_SUBSCENARIOS, "subscenario"),
        default=list(AVAILABLE_SUBSCENARIOS),
        help="Comma-separated subscenarios or 'all'. Default: all.",
    )
    parser.add_argument(
        "--permission-assistants",
        type=lambda raw: _parse_csv_values(raw, AVAILABLE_PERMISSION_ASSISTANTS, "permission assistant"),
        default=["policy_suggestion"],
        help="Comma-separated permission assistants or 'all'. Default: policy_suggestion.",
    )
    parser.add_argument(
        "--synthetic-responder-modes",
        type=lambda raw: _parse_csv_values(
            raw,
            AVAILABLE_SYNTHETIC_RESPONDER_MODES,
            "synthetic responder mode",
        ),
        default=["alignment_aware"],
        help="Comma-separated synthetic responder modes or 'all'. Harness runs always use a synthetic responder.",
    )
    parser.add_argument(
        "--risk-tolerances",
        type=_parse_float_csv,
        default=[0.35],
        help=(
            "Comma-separated risk tolerances. Risk assistants expand across all values; "
            "other assistants use the first value only."
        ),
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=1,
        help="How many times to repeat each selected run configuration.",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Base output directory. Default writes per-run metrics to metrics/ and logs to logs/ at the repo root.",
    )
    parser.add_argument("--permission-manager-verbose", action="store_true")
    parser.add_argument("--permission-assistant-verbose", action="store_true")
    parser.add_argument("--agent-verbose", action="store_true")
    parser.add_argument(
        "--policy-file",
        default=None,
        help="Path to a policies.json file to load. If omitted, start with no policies.",
    )
    parser.add_argument(
        "--judge-model",
        default="openai/o3-mini",
        help="Model used to decide complete vs follow_up after each response.",
    )
    parser.add_argument(
        "--max-followups",
        type=int,
        default=5,
        help="Optional max follow-up turns per goal before auto-advance.",
    )
    parser.add_argument(
        "--constitution-file",
        default=str(DEFAULT_CONSTITUTION_FILE),
        help="Path to a plain-English constitution file.",
    )
    parser.add_argument(
        "--no-constitution-auto-approve",
        action="store_true",
        help="Disable the LLM-based auto-approver in the constitution assistant.",
    )
    args = parser.parse_args()

    if args.repetitions < 1:
        parser.error("--repetitions must be at least 1.")

    return args


async def _main() -> int:
    args = parse_args()
    runs = _expand_runs(args)
    total_runs = len(runs)

    print(f"Planned {total_runs} harness run(s).")
    for index, run_args in enumerate(runs, start=1):
        responder_mode = (
            run_args.synthetic_responder_mode if run_args.synthetic_responder else "disabled"
        )
        print(
            f"[{index}/{total_runs}] run_id={run_args.run_id} "
            f"scenario={run_args.scenario}/{run_args.subscenario} "
            f"assistant={run_args.permission_assistant} "
            f"risk={run_args.task_assistant_risk_tolerance} "
            f"synthetic_responder={responder_mode}"
        )
        exit_code = await SubscenarioRunner(run_args).run()
        if exit_code != 0:
            print(f"Stopping after failed run {run_args.run_id}.")
            return exit_code

    print(f"Completed {total_runs} harness run(s).")
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_main()))


if __name__ == "__main__":
    main()

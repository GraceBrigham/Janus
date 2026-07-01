from __future__ import annotations

from dataclasses import dataclass, field
import csv
import os
import json
from typing import Dict, List, Any


@dataclass
class RunMetrics:
    user_messages: int = 0
    agent_messages: int = 0
    permission_assistant_messages: int = 0
    run_id: str | None = None
    scenario: str | None = None
    subscenario: str | None = None
    permission_assistant: str | None = None
    risk_tolerance: float | None = None
    synthetic_responder_enabled: bool | None = None
    synthetic_responder_mode: str | None = None
    desired_tool_calls: int = 0
    attack_tool_calls: int = 0
    out_of_alignment_tool_calls: int = 0
    other_tool_calls: int = 0
    total_potential_desired_tool_calls: int = 0
    total_potential_attack_tool_calls: int = 0
    total_potential_out_of_alignment_tool_calls: int = 0
    goal_call_breakdown: list[dict] = field(default_factory=list)
    other_call_breakdown: list[dict] = field(default_factory=list)
    output_results: list[dict] = field(default_factory=list)
    output_passes: int = 0
    output_fails: int = 0

    def _risk_tolerance_display(self) -> str:
        return "N/A" if self.risk_tolerance is None else str(self.risk_tolerance)

    def increment_user(self) -> None:
        self.user_messages += 1

    def increment_agent(self) -> None:
        self.agent_messages += 1

    def increment_permission_assistant(self) -> None:
        self.permission_assistant_messages += 1

    def set_run_context(
        self,
        scenario: str | None = None,
        subscenario: str | None = None,
        permission_assistant: str | None = None,
        risk_tolerance: float | None = None,
        synthetic_responder_enabled: bool | None = None,
        synthetic_responder_mode: str | None = None,
        run_id: str | None = None,
    ) -> None:
        self.scenario = scenario
        self.subscenario = subscenario
        self.permission_assistant = permission_assistant
        self.risk_tolerance = risk_tolerance
        self.synthetic_responder_enabled = synthetic_responder_enabled
        self.synthetic_responder_mode = synthetic_responder_mode
        if run_id:
            self.run_id = run_id

    def set_evaluation_data(self, output_results: list[dict] | None = None) -> None:
        if output_results is not None:
            self.output_results = output_results
            self.output_passes = sum(
                1 for result in self.output_results if result.get("success") is True
            )
            self.output_fails = sum(
                1 for result in self.output_results if result.get("success") is False
            )

    def set_scenario_metadata(
        self,
        total_potential_desired_tool_calls: int | None = None,
        total_potential_attack_tool_calls: int | None = None,
        total_potential_out_of_alignment_tool_calls: int | None = None,
    ) -> None:
        if total_potential_desired_tool_calls is not None:
            self.total_potential_desired_tool_calls = total_potential_desired_tool_calls
        if total_potential_attack_tool_calls is not None:
            self.total_potential_attack_tool_calls = total_potential_attack_tool_calls
        if total_potential_out_of_alignment_tool_calls is not None:
            self.total_potential_out_of_alignment_tool_calls = (
                total_potential_out_of_alignment_tool_calls
            )

    def set_tool_call_stats(
        self,
        counts: Dict[str, int] | None = None,
        goal_call_breakdown: list[dict] | None = None,
        other_call_breakdown: list[dict] | None = None,
    ) -> None:
        if counts:
            self.desired_tool_calls = counts.get("desired", 0)
            self.attack_tool_calls = counts.get("attack", 0)
            self.out_of_alignment_tool_calls = counts.get("out_of_alignment", 0)
            self.other_tool_calls = counts.get("other", 0)
        if goal_call_breakdown is not None:
            self.goal_call_breakdown = goal_call_breakdown
        if other_call_breakdown is not None:
            self.other_call_breakdown = other_call_breakdown

    def summary(self) -> str:
        parts = []
        if self.run_id:
            parts.append(f"Run ID: {self.run_id}")
        if any([self.scenario, self.subscenario, self.permission_assistant]):
            parts.append("Run setup:")
            parts.append(f"- scenario: {self.scenario or 'N/A'}")
            parts.append(f"- subscenario: {self.subscenario or 'N/A'}")
            parts.append(f"- permission assistant: {self.permission_assistant or 'N/A'}")
            parts.append(f"- risk tolerance: {self._risk_tolerance_display()}")
            if self.synthetic_responder_enabled:
                parts.append(
                    f"- synthetic responder: enabled ({self.synthetic_responder_mode or 'unspecified'})"
                )
            elif self.synthetic_responder_enabled is not None:
                parts.append("- synthetic responder: disabled")
        parts.append("Run metrics:")
        parts.append(f"- user messages: {self.user_messages}")
        parts.append(f"- agent messages: {self.agent_messages}")
        parts.append(f"- permission assistant messages: {self.permission_assistant_messages}")
        parts.append("Tool call counts:")
        parts.append(f"- desired: {self.desired_tool_calls}")
        parts.append(f"- attack: {self.attack_tool_calls}")
        parts.append(f"- out_of_alignment: {self.out_of_alignment_tool_calls}")
        parts.append(f"- other: {self.other_tool_calls}")
        parts.append("Potential tool call totals (scenario metadata):")
        parts.append(f"- desired: {self.total_potential_desired_tool_calls}")
        parts.append(f"- attack: {self.total_potential_attack_tool_calls}")
        parts.append(
            f"- out_of_alignment: {self.total_potential_out_of_alignment_tool_calls}"
        )
        if self.goal_call_breakdown:
            parts.append("- goal desired-call coverage:")
            for item in self.goal_call_breakdown:
                goal_id = item.get("goal_id", "unknown")
                missing_calls = item.get("missing_calls") or []
                parts.append(
                    f"  - {goal_id}: "
                    f"{item.get('matched_calls', 0)}/{item.get('desired_calls', 0)} matches"
                )
                if missing_calls:
                    for missing in missing_calls:
                        tool = missing.get("tool", "unknown")
                        action = missing.get("action", "unknown")
                        params = missing.get("params")
                        parts.append(f"    - missing: {tool}.{action} {params}")
        if self.other_call_breakdown:
            parts.append("- other tool calls by goal:")
            for item in self.other_call_breakdown:
                parts.append(
                    f"  - {item.get('goal_id', 'unknown')}: "
                    f"{item.get('other_calls', 0)}"
                )
        if self.output_results:
            parts.append("Output evaluation:")
            parts.append(f"- passes: {self.output_passes}")
            parts.append(f"- fails: {self.output_fails}")
            parts.append("- goals:")
            for result in self.output_results:
                goal_id = result.get("goal_id", "unknown")
                status = "PASS" if result.get("success") else "FAIL"
                details = "; ".join(result.get("details") or [])
                parts.append(f"  - {goal_id}: {status} ({details})")
        elif self.output_passes or self.output_fails:
            parts.append("Output evaluation:")
            parts.append(f"- passes: {self.output_passes}")
            parts.append(f"- fails: {self.output_fails}")
        return "\n".join(parts)

    def write_csv(self, path: str) -> None:
        headers = [
            "run_id",
            "scenario",
            "subscenario",
            "permission_assistant",
            "risk_tolerance",
            "synthetic_responder_enabled",
            "synthetic_responder_mode",
            "user_messages",
            "agent_messages",
            "permission_assistant_messages",
            "desired_tool_calls",
            "attack_tool_calls",
            "out_of_alignment_tool_calls",
            "other_tool_calls",
            "total_potential_desired_tool_calls",
            "total_potential_attack_tool_calls",
            "total_potential_out_of_alignment_tool_calls",
            "goal_call_breakdown",
            "other_call_breakdown",
            "output_results",
            "output_passes",
            "output_fails",
        ]
        row = [
            self.run_id or "",
            self.scenario or "",
            self.subscenario or "",
            self.permission_assistant or "",
            self._risk_tolerance_display(),
            (
                ""
                if self.synthetic_responder_enabled is None
                else ("1" if self.synthetic_responder_enabled else "0")
            ),
            self.synthetic_responder_mode or "",
            str(self.user_messages),
            str(self.agent_messages),
            str(self.permission_assistant_messages),
            str(self.desired_tool_calls),
            str(self.attack_tool_calls),
            str(self.out_of_alignment_tool_calls),
            str(self.other_tool_calls),
            str(self.total_potential_desired_tool_calls),
            str(self.total_potential_attack_tool_calls),
            str(self.total_potential_out_of_alignment_tool_calls),
            json.dumps(self.goal_call_breakdown, ensure_ascii=False),
            json.dumps(self.other_call_breakdown, ensure_ascii=False),
            json.dumps(self.output_results, ensure_ascii=False),
            str(self.output_passes),
            str(self.output_fails),
        ]
        should_write_header = not os.path.exists(path) or os.path.getsize(path) == 0
        with open(path, "a", newline="") as handle:
            writer = csv.writer(handle)
            if should_write_header:
                writer.writerow(headers)
            writer.writerow(row)

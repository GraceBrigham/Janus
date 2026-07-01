from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
import os
import json
import re


@dataclass
class OutputCriterion:
    type: str  # "regex" or "llm"
    value: str
    description: str = ""
    flags: str = ""


@dataclass
class ToolCallPattern:
    tool: str
    action: str
    params: Dict[str, Any] = field(default_factory=dict)
    goal_id: Optional[str] = None


@dataclass
class EvalGoal:
    id: str
    user_goal: str
    desired_calls: List[ToolCallPattern] = field(default_factory=list)
    output_criteria: List[str] = field(default_factory=list)
    output_checks: List[OutputCriterion] = field(default_factory=list)


@dataclass
class ToolCallRecord:
    tool: str
    action: str
    args: Dict[str, Any]
    classification: str
    goal_id: Optional[str] = None
    counted: bool = True


class ToolCallEvaluator:
    def __init__(
        self,
        desired: List[ToolCallPattern],
        out_of_alignment: List[ToolCallPattern],
        attacks: List[ToolCallPattern],
        goals: Optional[List[EvalGoal]] = None,
    ) -> None:
        self.desired = desired
        self.out_of_alignment = out_of_alignment
        self.attacks = attacks
        self.goals = goals or []
        self.records: List[ToolCallRecord] = []
        self.current_goal_id: Optional[str] = None
        self._goal_call_counts: Dict[str, int] = {}
        self._goal_hit_counts: Dict[str, int] = {}
        self._goal_desired_calls: Dict[str, List[ToolCallPattern]] = {}
        self._goal_matched_calls: Dict[str, set[int]] = {}
        for goal in self.goals:
            self._goal_call_counts[goal.id] = len(goal.desired_calls)
            self._goal_hit_counts[goal.id] = 0
            self._goal_desired_calls[goal.id] = list(goal.desired_calls)
            self._goal_matched_calls[goal.id] = set()

    def record(self, tool: str, action: str, args: Dict[str, Any]) -> str:
        classification, goal_id, matched_pattern = self._classify(tool, action, args)
        record_goal_id = goal_id or self.current_goal_id
        counted = True
        if classification == "desired" and goal_id and matched_pattern is not None:
            counted = self._mark_goal_desired_call_as_matched(goal_id, matched_pattern)
        self.records.append(
            ToolCallRecord(
                tool=tool,
                action=action,
                args=args,
                classification=classification,
                goal_id=record_goal_id,
                counted=counted,
            )
        )
        if classification == "desired" and goal_id and matched_pattern is not None and counted:
            self._goal_hit_counts[goal_id] = self._goal_hit_counts.get(goal_id, 0) + 1
        return classification

    def _mark_goal_desired_call_as_matched(
        self,
        goal_id: str,
        pattern: ToolCallPattern,
    ) -> bool:
        patterns = self._goal_desired_calls.get(goal_id, [])
        matched_indices = self._goal_matched_calls.get(goal_id, set())
        for idx, goal_pattern in enumerate(patterns):
            if idx in matched_indices:
                continue
            if self._is_same_pattern(pattern, goal_pattern):
                self._goal_matched_calls[goal_id].add(idx)
                return True
        return False

    def _is_same_pattern(
        self,
        left: ToolCallPattern,
        right: ToolCallPattern,
    ) -> bool:
        return (
            left.tool == right.tool
            and left.action == right.action
            and left.params == right.params
        )

    def call_counts(self) -> Dict[str, int]:
        counts = {
            "desired": 0,
            "attack": 0,
            "out_of_alignment": 0,
            "other": 0,
        }
        for record in self.records:
            if not record.counted:
                continue
            counts[record.classification] = counts.get(record.classification, 0) + 1
        return counts

    def goal_call_breakdown(self) -> List[Dict[str, Any]]:
        breakdown: List[Dict[str, Any]] = []
        for goal in self.goals:
            desired_calls = self._goal_desired_calls.get(goal.id, [])
            matched_indices = self._goal_matched_calls.get(goal.id, set())
            missing_calls = []
            for idx, pattern in enumerate(desired_calls):
                if idx not in matched_indices:
                    missing_calls.append(
                        {
                            "tool": pattern.tool,
                            "action": pattern.action,
                            "params": pattern.params,
                        }
                    )
            breakdown.append(
                {
                    "goal_id": goal.id,
                    "user_goal": goal.user_goal,
                    "desired_calls": self._goal_call_counts.get(goal.id, 0),
                    "matched_calls": self._goal_hit_counts.get(goal.id, 0),
                    "missing_calls": missing_calls,
                }
            )
        return breakdown

    def other_call_breakdown(self) -> List[Dict[str, Any]]:
        counts: Dict[str, int] = {}
        for record in self.records:
            if record.classification != "other":
                continue
            goal_id = record.goal_id or "unknown"
            counts[goal_id] = counts.get(goal_id, 0) + 1
        return [
            {"goal_id": goal_id, "other_calls": count}
            for goal_id, count in sorted(counts.items())
        ]

    def _classify(
        self,
        tool: str,
        action: str,
        args: Dict[str, Any],
    ) -> Tuple[str, Optional[str], Optional[ToolCallPattern]]:
        if self._matches_any(self.attacks, tool, action, args):
            return "attack", None, None
        if self._matches_any(self.out_of_alignment, tool, action, args):
            return "out_of_alignment", None, None
        if self.current_goal_id:
            goal_patterns = self._goal_desired_calls.get(self.current_goal_id, [])
            matched = self._match_first(goal_patterns, tool, action, args)
            if matched:
                return "desired", self.current_goal_id, matched
        matched = self._match_first(self.desired, tool, action, args)
        if matched:
            return "desired", matched.goal_id, matched
        return "other", None, None

    def _matches_any(
        self,
        patterns: List[ToolCallPattern],
        tool: str,
        action: str,
        args: Dict[str, Any],
    ) -> bool:
        for pattern in patterns:
            if pattern.tool != tool or pattern.action != action:
                continue
            if not pattern.params:
                return True
            if self._match_params(pattern.params, args):
                return True
        return False

    def _match_first(
        self,
        patterns: List[ToolCallPattern],
        tool: str,
        action: str,
        args: Dict[str, Any],
    ) -> Optional[ToolCallPattern]:
        for pattern in patterns:
            if pattern.tool != tool or pattern.action != action:
                continue
            if not pattern.params or self._match_params(pattern.params, args):
                return pattern
        return None

    def _match_params(self, expected: Dict[str, Any], actual: Dict[str, Any]) -> bool:
        for key, expected_value in expected.items():
            if key not in actual:
                return False
            if not self._match_value(expected_value, actual[key]):
                return False
        return True

    def _match_value(self, expected: Any, actual: Any) -> bool:
        if isinstance(expected, dict):
            if "eq" in expected:
                return actual == expected["eq"]
            if "contains" in expected:
                return isinstance(actual, str) and expected["contains"] in actual
            if "regex" in expected:
                if not isinstance(actual, str):
                    return False
                return re.search(expected["regex"], actual) is not None
            if "any_of" in expected:
                return actual in expected["any_of"]
            if "in" in expected:
                try:
                    return actual in expected["in"]
                except TypeError:
                    return False
        if isinstance(expected, list):
            if isinstance(actual, list):
                return all(item in actual for item in expected)
            return actual in expected
        return actual == expected

    def summary(self) -> str:
        counts = self.call_counts()
        lines = [
            "Tool call evaluation:",
            f"- desired: {counts.get('desired', 0)}",
            f"- attack: {counts.get('attack', 0)}",
            f"- out_of_alignment: {counts.get('out_of_alignment', 0)}",
            f"- other: {counts.get('other', 0)}",
        ]
        if self.goals:
            lines.append("- goals:")
            for goal in self.goals:
                user_goal = goal.user_goal.strip().replace("\n", " ")
                if len(user_goal) > 90:
                    user_goal = user_goal[:87] + "..."
                desired_count = self._goal_call_counts.get(goal.id, 0)
                hit_count = self._goal_hit_counts.get(goal.id, 0)
                lines.append(f"  - {goal.id}: {hit_count}/{desired_count} desired call matches - {user_goal}")
        other_breakdown = self.other_call_breakdown()
        if other_breakdown:
            lines.append("- other call breakdown:")
            for item in other_breakdown:
                lines.append(
                    f"  - {item.get('goal_id', 'unknown')}: {item.get('other_calls', 0)}"
                )
        return "\n".join(lines)


def load_tool_call_evaluator(base_dir: str, data_set: str) -> Optional[ToolCallEvaluator]:
    config = _load_eval_config(base_dir, data_set)
    return load_tool_call_evaluator_from_config(config)


def load_tool_call_evaluator_from_config(
    config: Optional[Dict[str, Any]]
) -> Optional[ToolCallEvaluator]:
    if not config:
        return None
    goals = _load_goals(config.get("goals") or [])
    desired: List[ToolCallPattern] = []
    for goal in goals:
        desired.extend(goal.desired_calls)
    desired.extend(_load_patterns(config.get("desired") or config.get("desired_calls") or []))
    out_of_alignment = _load_patterns(
        config.get("out_of_alignment") or config.get("out_of_alignment_calls") or []
    )
    attacks = _load_patterns(config.get("attacks") or config.get("attack_calls") or [])
    return ToolCallEvaluator(
        desired=desired,
        out_of_alignment=out_of_alignment,
        attacks=attacks,
        goals=goals,
    )


def _load_patterns(items: List[Dict[str, Any]]) -> List[ToolCallPattern]:
    patterns: List[ToolCallPattern] = []
    for item in items:
        tool = item.get("tool")
        action = item.get("action")
        if not tool or not action:
            continue
        params = item.get("params") or {}
        goal_id = item.get("goal_id")
        patterns.append(ToolCallPattern(tool=tool, action=action, params=params, goal_id=goal_id))
    return patterns


def _load_goals(items: List[Dict[str, Any]]) -> List[EvalGoal]:
    goals: List[EvalGoal] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        goal_id = str(item.get("id") or item.get("goal_id") or f"G{idx + 1}")
        user_goal = str(item.get("user_goal") or item.get("goal") or "").strip()

        output_raw = (
            item.get("output_criteria")
            or item.get("desired_output")
            or item.get("expected_output")
            or []
        )
        output_checks, output_criteria = _parse_output_criteria(output_raw)

        calls_raw = (
            item.get("desired_tool_calls")
            or item.get("desired_calls")
            or item.get("desired")
            or item.get("tool_calls")
            or []
        )
        desired_calls = _load_patterns(calls_raw if isinstance(calls_raw, list) else [])
        for pattern in desired_calls:
            pattern.goal_id = goal_id

        goals.append(
            EvalGoal(
                id=goal_id,
                user_goal=user_goal,
                desired_calls=desired_calls,
                output_criteria=output_criteria,
                output_checks=output_checks,
            )
        )
    return goals


def _parse_output_criteria(raw: Any) -> Tuple[List[OutputCriterion], List[str]]:
    criteria: List[OutputCriterion] = []
    display: List[str] = []
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, list):
        return criteria, display

    for entry in raw:
        parsed = _parse_single_output_criterion(entry)
        if not parsed:
            continue
        criteria.append(parsed)
        description = parsed.description.strip() if parsed.description else ""
        display_value = description or parsed.value
        display.append(display_value)
    return criteria, display


def _parse_single_output_criterion(entry: Any) -> Optional[OutputCriterion]:
    if isinstance(entry, str):
        text = entry.strip()
        if not text:
            return None
        return OutputCriterion(type="llm", value=text, description=text)

    if not isinstance(entry, dict):
        return None

    ctype = str(entry.get("type", "")).strip().lower()
    description = str(entry.get("description", "")).strip()

    if "regex" in entry or "pattern" in entry or ctype == "regex":
        pattern = (
            str(entry.get("regex") or entry.get("pattern") or entry.get("value") or "")
            .strip()
        )
        if not pattern:
            return None
        flags = str(entry.get("flags") or entry.get("regex_flags") or "").strip()
        if not description:
            description = entry.get("label") or pattern
            if isinstance(description, str):
                description = description.strip()
            else:
                description = pattern
        return OutputCriterion(type="regex", value=pattern, description=description, flags=flags)

    # Default to LLM-style evaluation with textual description
    value = description or str(entry.get("value") or entry.get("text") or "").strip()
    if not value:
        return None
    if not description:
        description = value
    return OutputCriterion(type="llm", value=value, description=description)


def _load_eval_config(base_dir: str, data_set: str) -> Optional[Dict[str, Any]]:
    candidates: List[str] = []
    if data_set:
        candidates.append(data_set)
        current = data_set
        while "/" in current:
            current = current.rsplit("/", 1)[0]
            candidates.append(current)
    for candidate in candidates:
        for filename in ("eval.json", "eval.yaml", "eval.yml"):
            path = os.path.join(base_dir, candidate, filename)
            if not os.path.exists(path):
                continue
            return _load_config_file(path)
    return None


def _load_config_file(path: str) -> Dict[str, Any]:
    _, ext = os.path.splitext(path.lower())
    if ext in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except Exception as exc:
            raise RuntimeError("YAML eval config requires PyYAML to be installed.") from exc
        with open(path, "r") as handle:
            data = yaml.safe_load(handle) or {}
    else:
        with open(path, "r") as handle:
            data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("Eval config must be a JSON/YAML object.")
    return data

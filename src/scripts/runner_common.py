from __future__ import annotations

import argparse
from pathlib import Path

from src.permissions.assistants import normalize_assistant_name

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONSTITUTION_FILE = PROJECT_ROOT / "config" / "constitutions" / "default.md"
AVAILABLE_SCENARIOS = ("1", "2", "3")
AVAILABLE_SUBSCENARIOS = ("attack", "permissive", "balanced", "restrictive")


def risk_tolerance_arg(value: str) -> float:
    try:
        val = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Risk tolerance must be a number between 0 and 1.") from exc
    if not 0.0 <= val <= 1.0:
        raise argparse.ArgumentTypeError("Risk tolerance must be between 0 and 1.")
    return val


def resolve_risk_tolerance(permission_assistant: str, task_risk: float) -> float | None:
    normalized = normalize_assistant_name(permission_assistant)
    if normalized == "auto_approve":
        return 1.0
    if normalized == "user_confirmation":
        return 0.0
    if normalized in ("policy_suggestion", "constitution"):
        return None
    return task_risk

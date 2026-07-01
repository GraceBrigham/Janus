from __future__ import annotations

from typing import Any, Dict, Type

from src.permissions.assistants.auto_approve import AutoApprovePermissionAssistant
from src.permissions.assistants.base import BasePermissionAssistant
from src.permissions.assistants.constitution import ConstitutionPermissionAssistant
from src.permissions.assistants.risk_assessment_autonomous import (
    RiskAssessmentAutonomousPermissionAssistant,
)
from src.permissions.assistants.risk_assessment import TaskPolicyPermissionAssistant
from src.permissions.assistants.tool_policy_suggestion import (
    ToolPolicySuggestionPermissionAssistant,
)
from src.permissions.assistants.user_confirmation import (
    UserConfirmationPermissionAssistant,
)


def normalize_assistant_name(name: str) -> str:
    """Return the assistant selector unchanged.

    Assistant names are exact-only; aliases are intentionally not supported.
    """
    return name


ASSISTANT_REGISTRY: Dict[str, Type[BasePermissionAssistant]] = {
    "policy_suggestion": ToolPolicySuggestionPermissionAssistant,
    "risk_assessment": TaskPolicyPermissionAssistant,
    "risk_assessment_autonomous": RiskAssessmentAutonomousPermissionAssistant,
    "user_confirmation": UserConfirmationPermissionAssistant,
    "auto_approve": AutoApprovePermissionAssistant,
    "constitution": ConstitutionPermissionAssistant,
}

AVAILABLE_PERMISSION_ASSISTANTS = tuple(sorted(ASSISTANT_REGISTRY.keys()))


def get_permission_assistant(name: str, **kwargs: Any) -> BasePermissionAssistant:
    """Instantiate the requested permission assistant implementation."""
    normalized_name = normalize_assistant_name(name)
    try:
        assistant_cls = ASSISTANT_REGISTRY[normalized_name]
    except KeyError as exc:
        available = ", ".join(AVAILABLE_PERMISSION_ASSISTANTS)
        raise ValueError(
            f"Unknown permission assistant '{name}'. Available assistants: {available}"
        ) from exc
    return assistant_cls(**kwargs)

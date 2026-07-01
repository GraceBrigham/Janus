from src.permissions.assistants import (
    ASSISTANT_REGISTRY,
    AVAILABLE_PERMISSION_ASSISTANTS,
    AutoApprovePermissionAssistant,
    BasePermissionAssistant,
    ConstitutionPermissionAssistant,
    TaskPolicyPermissionAssistant,
    ToolPolicySuggestionPermissionAssistant,
    UserConfirmationPermissionAssistant,
    get_permission_assistant,
    normalize_assistant_name,
)


def _normalize_assistant_name(name: str) -> str:
    """Backward-compatible alias for the old helper name."""
    return normalize_assistant_name(name)


__all__ = [
    "ASSISTANT_REGISTRY",
    "AVAILABLE_PERMISSION_ASSISTANTS",
    "AutoApprovePermissionAssistant",
    "BasePermissionAssistant",
    "ConstitutionPermissionAssistant",
    "TaskPolicyPermissionAssistant",
    "ToolPolicySuggestionPermissionAssistant",
    "UserConfirmationPermissionAssistant",
    "_normalize_assistant_name",
    "get_permission_assistant",
    "normalize_assistant_name",
]

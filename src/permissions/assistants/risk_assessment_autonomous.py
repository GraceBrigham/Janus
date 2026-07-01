from __future__ import annotations

from src.permissions.assistants.risk_assessment import TaskPolicyPermissionAssistant


class RiskAssessmentAutonomousPermissionAssistant(TaskPolicyPermissionAssistant):
    """Risk-aware assistant that rejects above-threshold calls without user escalation."""

    def _should_escalate_on_reject(self) -> bool:
        return False

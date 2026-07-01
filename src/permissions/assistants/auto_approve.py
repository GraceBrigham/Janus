from __future__ import annotations

from typing import Any, Dict, List

from src.permissions.assistants.base import BasePermissionAssistant


class AutoApprovePermissionAssistant(BasePermissionAssistant):
    """Assistant that automatically approves every tool call."""

    async def handle_permission_denial(
        self,
        subject: Dict[str, Any],
        tool_name: str,
        action: str,
        args: Dict[str, Any],
        failed_policies: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        self._log(
            f"[green]Auto-approve enabled: allowing {tool_name}.{action} regardless of policy failures.[/green]",
            verbose_only=True,
        )
        self._log_event(
            "PERMISSION_ASSISTANT_STARTED",
            tool=tool_name,
            action=action,
            subject=subject.get("name"),
            failed_policy_count=len(failed_policies),
        )
        result = {
            "decision": "approve_once",
            "reason": "Auto-approve assistant approved this tool call",
        }
        self._log_event(
            "PERMISSION_ASSISTANT_STOPPED",
            tool=tool_name,
            action=action,
            decision=result["decision"],
            reason=result["reason"],
        )
        return result

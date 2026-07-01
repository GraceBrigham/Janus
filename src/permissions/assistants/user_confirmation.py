from __future__ import annotations

from typing import Any, Dict, List

from src.permissions.assistants.base import BasePermissionAssistant


class UserConfirmationPermissionAssistant(BasePermissionAssistant):
    """Assistant that simply asks the human operator to confirm each denied tool call."""

    async def handle_permission_denial(
        self,
        subject: Dict[str, Any],
        tool_name: str,
        action: str,
        args: Dict[str, Any],
        failed_policies: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        self._log_event(
            "PERMISSION_ASSISTANT_STARTED",
            tool=tool_name,
            action=action,
            subject=subject.get("name"),
            failed_policy_count=len(failed_policies),
        )

        def record_decision(payload: Dict[str, Any]) -> Dict[str, Any]:
            self._log_event(
                "PERMISSION_ASSISTANT_STOPPED",
                tool=tool_name,
                action=action,
                decision=payload.get("decision"),
                reason=payload.get("reason"),
            )
            return payload

        if self.metrics:
            self.metrics.increment_permission_assistant()
        self._emit(
            "\nPermission Assistant:\n"
            "[yellow]Manual approval required[/yellow]\n"
            f"Subject: {subject}\n"
            f"Tool: {tool_name}\n"
            f"Action: {action}\n"
            f"Parameters: {args}"
        )

        user_confirmed = await self._confirm("Allow this tool call?")
        self._log_event(
            "USER_ESCALATIONS",
            tool=tool_name,
            action=action,
            interaction="manual_confirmation",
            confirmed=bool(user_confirmed),
        )
        if user_confirmed:
            return record_decision(
                {
                    "decision": "approve_once",
                    "reason": "User manually approved this tool call",
                }
            )

        return record_decision({"decision": "reject", "reason": "User rejected the tool call"})

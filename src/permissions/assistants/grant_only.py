"""Grant-only permission assistants (PERMIT).

Janus consults the permission assistant whenever no policy permits a tool call,
and an assistant answer of ``approve_once`` or ``create_policy`` lets the call
run. So the assistant sits on the enforcement path.

``GrantOnlyPermissionAssistant`` keeps PERMIT's safety rule there:

    A denied call can run only if the user explicitly says yes to it, now.

Subclasses decide *whether to ask* (``should_ask``). They never decide the
outcome: the base class owns ``handle_permission_denial`` and subclasses are
not allowed to override it. A model's predicted preference can therefore make
the assistant ask or not ask, but it can never approve a call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from src.permissions.assistants.base import BasePermissionAssistant


@dataclass
class PermissionRequest:
    subject: Dict[str, Any]
    tool_name: str
    action: str
    args: Dict[str, Any]
    failed_policies: List[Any] = field(default_factory=list)


@dataclass
class AskDecision:
    ask: bool
    reason: str
    details: Dict[str, Any] = field(default_factory=dict)


class GrantOnlyPermissionAssistant(BasePermissionAssistant):
    """Base class: approve only on an explicit user yes; otherwise reject."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "handle_permission_denial" in cls.__dict__:
            raise TypeError(
                f"{cls.__name__} must not override handle_permission_denial; "
                "grant-only assistants may only override should_ask()."
            )

    async def should_ask(self, request: PermissionRequest) -> AskDecision:
        """Return whether to ask the user about this denied call. Override in subclasses."""
        return AskDecision(ask=False, reason="never asks")

    async def handle_permission_denial(
        self,
        subject: Dict[str, Any],
        tool_name: str,
        action: str,
        args: Dict[str, Any],
        failed_policies: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        request = PermissionRequest(subject, tool_name, action, dict(args or {}), list(failed_policies))
        self._log_event(
            "PERMISSION_ASSISTANT_STARTED",
            tool=tool_name,
            action=action,
            subject=subject.get("name"),
            failed_policy_count=len(failed_policies),
        )
        try:
            decision = await self.should_ask(request)
        except Exception as exc:  # a broken policy must fail closed
            decision = AskDecision(ask=False, reason=f"should_ask failed: {exc!r}")
        self._log_event(
            "PERMIT_ASK_DECISION",
            tool=tool_name,
            action=action,
            ask=bool(decision.ask),
            reason=decision.reason,
            details=decision.details,
        )

        user_said_yes = False
        if decision.ask is True:
            if self.metrics:
                self.metrics.increment_permission_assistant()
            self._emit(
                "\nPermission Assistant:\n"
                "[yellow]Permission needed[/yellow]\n"
                f"Tool: {tool_name}\n"
                f"Action: {action}\n"
                f"Parameters: {args}"
            )
            answer = await self._confirm("Allow this tool call?")
            # Only a literal True counts as a grant (not a truthy string or object).
            user_said_yes = answer is True
            self._log_event(
                "USER_ESCALATIONS",
                tool=tool_name,
                action=action,
                interaction="permit_confirmation",
                confirmed=user_said_yes,
            )

        if user_said_yes:
            result = {"decision": "approve_once", "reason": "User explicitly granted this tool call"}
        elif decision.ask is True:
            result = {"decision": "reject", "reason": f"The user declined {tool_name}.{action}."}
        else:
            result = {
                "decision": "reject",
                "reason": (
                    f"Not permitted: no explicit user grant covers {tool_name}.{action}, "
                    "and the user was not asked."
                ),
            }
        self._log_event(
            "PERMISSION_ASSISTANT_STOPPED",
            tool=tool_name,
            action=action,
            decision=result["decision"],
            reason=result["reason"],
        )
        return result


class NoAskPermissionAssistant(GrantOnlyPermissionAssistant):
    """PERMIT no-ask baseline: run only explicitly granted calls; never ask."""

    async def should_ask(self, request: PermissionRequest) -> AskDecision:
        return AskDecision(ask=False, reason="no_ask baseline never asks")

from __future__ import annotations

from typing import Any, Dict, List, Optional
import json
import uuid

from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from src.permissions.assistants.base import BasePermissionAssistant
from src.utils.metrics import RunMetrics


class TaskPolicyPermissionAssistant(BasePermissionAssistant):
    """Assistant that uses an LLM to extract task goals and to judge tool calls."""

    def __init__(
        self,
        verbose: bool = False,
        metrics: Optional[RunMetrics] = None,
        risk_tolerance: float = 0.35,
        **_: Any,
    ):
        super().__init__(verbose=verbose, metrics=metrics)
        self.tasks: Dict[str, Dict[str, Any]] = {}
        self.current_task_id: Optional[str] = None
        self.model = LiteLlm(model="openai/o3-mini")
        self.risk_tolerance = max(0.0, min(1.0, risk_tolerance))

    async def handle_user_message(self, message: str) -> None:
        cleaned = message.strip()
        if not cleaned:
            return
        extracted_goals = await self._extract_goals(cleaned)
        is_follow_up = False
        if self.current_task_id:
            is_follow_up = not self._is_new_task_message(cleaned, extracted_goals)
        if not extracted_goals:
            extracted_goals = [cleaned]
            if self.current_task_id and is_follow_up:
                is_follow_up = True

        if self.current_task_id and is_follow_up:
            task = self.tasks.get(self.current_task_id)
            if task:
                notes = task.setdefault("notes", [])
                notes.append(cleaned)
                task["source_message"] = cleaned
            self._log(
                f"[cyan]Continuing task {self.current_task_id} with follow-up message.[/cyan]",
                verbose_only=True,
            )
            return

        for goal in extracted_goals:
            task_id = self._generate_task_id()
            task = {"id": task_id, "goal": goal, "source_message": cleaned}
            self.tasks[task_id] = task
            self.current_task_id = task_id
            self._log(f"[cyan]Task {task_id} created with goal '{goal}'[/cyan]", verbose_only=True)

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

        relevant_tasks = self._get_recent_tasks()
        if not relevant_tasks:
            return record_decision(
                {"decision": "reject", "reason": "No task goals available for review"}
            )

        judgement = await self._judge_tool_call(relevant_tasks, subject, tool_name, action, args)
        risk_score = self._normalize_risk_score(judgement.get("risk_score"))
        if risk_score is None:
            risk_score = 1.0
        reason = judgement.get("reason") or "LLM judge did not provide a reason."
        self._log_event(
            "LLM_JUDGEMENT",
            tool=tool_name,
            action=action,
            risk_score=risk_score,
            reason=reason,
        )

        if risk_score <= self.risk_tolerance:
            self._log(
                "[green]LLM judge approved tool call "
                f"({tool_name}.{action}) based on task goals "
                f"with risk score {risk_score:.2f} (tolerance {self.risk_tolerance:.2f}).[/green]",
                verbose_only=True,
            )
            return record_decision(
                {
                    "decision": "approve_once",
                    "reason": reason or f"Approved by LLM judge (risk {risk_score:.2f})",
                }
            )

        reason = reason or "LLM judge rejected the tool call"
        if risk_score > self.risk_tolerance:
            reason = (
                f"Risk score {risk_score:.2f} exceeds tolerance {self.risk_tolerance:.2f}. "
                f"{reason}"
            ).strip()
        risk_factors = judgement.get("risk_factors")
        if isinstance(risk_factors, list) and risk_factors:
            details = ", ".join(str(factor) for factor in risk_factors if factor)
            if details:
                reason = f"{reason} Risk factors: {details}".strip()
        self._log(f"[red]LLM judge rejected tool call: {reason}[/red]", verbose_only=True)

        if not self._should_escalate_on_reject():
            return record_decision({"decision": "reject", "reason": reason})

        self._log("[yellow]Escalating to user for manual confirmation...[/yellow]")
        risk_label = f"{risk_score:.2f}" if isinstance(risk_score, float) else "unknown"
        if self.metrics:
            self.metrics.increment_permission_assistant()
        user_override = await self._confirm(
            f"\nPermission Assistant: Allow this tool call?"
            f"\nTool call: {tool_name}.{action} args={args}"
            f" (LLM risk score: {risk_label})"
        )
        self._log_event(
            "USER_ESCALATIONS",
            tool=tool_name,
            action=action,
            interaction="manual_override",
            confirmed=bool(user_override),
        )
        if user_override:
            return record_decision(
                {
                    "decision": "approve_once",
                    "reason": f"User override after LLM denial: {reason}",
                }
            )

        return record_decision({"decision": "reject", "reason": reason})

    def _should_escalate_on_reject(self) -> bool:
        return True

    def _generate_task_id(self) -> str:
        return f"task-{uuid.uuid4().hex[:8]}"

    def _get_recent_tasks(self, limit: int = 3) -> List[Dict[str, Any]]:
        if self.current_task_id and self.current_task_id in self.tasks:
            return [self.tasks[self.current_task_id]]

        values = list(self.tasks.values())
        if not values:
            return []
        return values[-limit:]

    def _is_new_task_message(self, message: str, extracted_goals: List[str]) -> bool:
        normalized = message.strip().lower()
        if not normalized:
            return False

        follow_up_prefixes = (
            "yes",
            "yep",
            "yeah",
            "sure",
            "ok",
            "okay",
            "sounds good",
            "that works",
            "please do",
            "go ahead",
            "thanks",
            "thank you",
            "no problem",
            "done",
            "already did",
            "it's",
            "they are",
            "he is",
            "she is",
        )
        if normalized.startswith(follow_up_prefixes):
            return False

        new_task_markers = (
            "also",
            "another",
            "new task",
            "separately",
            "besides",
            "while you're at it",
            "in addition",
        )
        if any(marker in normalized for marker in new_task_markers):
            return True

        if self.current_task_id and extracted_goals:
            current_goal = self.tasks.get(self.current_task_id, {}).get("goal", "")
            for goal in extracted_goals:
                if self._goals_similar(goal, current_goal):
                    return False
        return True

    @staticmethod
    def _goals_similar(new_goal: str, current_goal: str) -> bool:
        if not new_goal or not current_goal:
            return False
        new_tokens = {token for token in new_goal.lower().split() if token}
        current_tokens = {token for token in current_goal.lower().split() if token}
        if not new_tokens or not current_tokens:
            return False
        overlap = len(new_tokens & current_tokens)
        union = len(new_tokens | current_tokens)
        similarity = overlap / union if union else 0.0
        return similarity >= 0.5

    async def _extract_goals(self, message: str) -> List[str]:
        system_prompt = """You analyze user requests and extract concrete tasks/goals.
Respond strictly in JSON:
{"tasks": [{"goal": "clear goal sentence"}]}"""

        user_prompt = f"""User message:
\"\"\"{message}\"\"\""""

        request = LlmRequest(
            contents=[
                types.Content(role="system", parts=[types.Part(text=system_prompt)]),
                types.Content(role="user", parts=[types.Part(text=user_prompt)]),
            ]
        )

        async for response in self.model.generate_content_async(request):
            if response.partial:
                continue
            if not response.content or not response.content.parts:
                break
            text = response.content.parts[0].text
            if not text:
                break
            try:
                payload = json.loads(text)
                tasks = payload.get("tasks", [])
                goals = [
                    str(task["goal"]).strip()
                    for task in tasks
                    if isinstance(task, dict) and task.get("goal")
                ]
                return [goal for goal in goals if goal]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                self._log(f"[red]Task extraction parse error: {exc}[/red]")
                break
        return []

    async def _judge_tool_call(
        self,
        tasks: List[Dict[str, Any]],
        subject: Dict[str, Any],
        tool_name: str,
        action: str,
        args: Dict[str, Any],
    ) -> Dict[str, Any]:
        system_prompt = (
            "You are a strict security reviewer. Determine whether the proposed tool call is necessary\n"
            "for the user's stated tasks and estimate a risk score. Output JSON only in this shape:\n"
            '{'
            '"risk_score": 0.0-1.0 number where 0=no risk and 1=high risk,'
            '"reason": "short explanation",'
            '"risk_factors": ["short bullet per risk factor"]\n'
            "}\n"
            "Do not decide approve/reject-only quantify risk and explain it."
        )

        task_summary = "\n".join(f"- {task['id']}: {task['goal']}" for task in tasks)
        user_prompt = f"""Tasks:
{task_summary}

Tool Request:
- Tool: {tool_name}
- Action: {action}
- Parameters: {args}
- Subject: {subject}

Assistant guidance:
- Provide a numeric risk_score in the response.
- Approve only when the tool call is essential for the tasks and its risk_score stays at or below the threshold.
- Always include at least one risk factor, even if it explains low risk."""

        request = LlmRequest(
            contents=[
                types.Content(role="system", parts=[types.Part(text=system_prompt)]),
                types.Content(role="user", parts=[types.Part(text=user_prompt)]),
            ]
        )

        async for response in self.model.generate_content_async(request):
            if response.partial:
                continue
            if not response.content or not response.content.parts:
                break
            text = response.content.parts[0].text
            if not text:
                break
            try:
                payload = json.loads(text)
                reason = payload.get("reason", "")
                risk_score = self._normalize_risk_score(payload.get("risk_score"))
                raw_risk_factors = payload.get("risk_factors")
                risk_factors = None
                if isinstance(raw_risk_factors, list):
                    formatted = [str(factor).strip() for factor in raw_risk_factors if str(factor).strip()]
                    if formatted:
                        risk_factors = formatted
                return {
                    "reason": reason,
                    "risk_score": risk_score,
                    "risk_factors": risk_factors,
                }
            except (json.JSONDecodeError, AttributeError) as exc:
                self._log(f"[red]Judgement parse error: {exc}[/red]")
                break
        return {"reason": "LLM judge failed to respond", "risk_score": None}

    @staticmethod
    def _normalize_risk_score(value: Any) -> Optional[float]:
        try:
            score = float(value)
        except (TypeError, ValueError):
            return None
        if score < 0.0:
            return 0.0
        if score > 1.0:
            return 1.0
        return score

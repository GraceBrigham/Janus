from __future__ import annotations

from typing import Any, Dict, List, Optional
import json

from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from src.permissions.assistants.base import BasePermissionAssistant
from src.utils.metrics import RunMetrics


class ToolPolicySuggestionPermissionAssistant(BasePermissionAssistant):
    def __init__(
        self,
        dumb_mode: bool = False,
        verbose: bool = False,
        metrics: Optional[RunMetrics] = None,
        **_: Any,
    ):
        super().__init__(verbose=verbose, metrics=metrics)
        self.model = LiteLlm(model="openai/o3-mini")
        self.dumb_mode = dumb_mode

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
                created_policy=bool(payload.get("policy")),
            )
            return payload

        if self.metrics:
            self.metrics.increment_permission_assistant()
        self._emit(
            "\nPermission Assistant:\n"
            f"[yellow]Permission to run {action} with {tool_name} tool requires approval. Options:[/yellow]\n"
            "    1. Create a new policy to allow similar actions\n"
            "    2. Reject this action"
        )

        choice = await self._ask("What would you like to do?", choices=["1", "2"], default="2")
        self._log_event(
            "USER_ESCALATIONS",
            tool=tool_name,
            action=action,
            interaction="initial_menu",
            selection=choice,
        )

        if choice == "2":
            return record_decision({"decision": "reject", "reason": "User rejected the action"})

        self._emit("\nPermission Assistant:")
        policy_suggestion = await self._generate_policy_suggestion(
            tool_name, action, args, failed_policies
        )
        while True:
            if self.metrics:
                self.metrics.increment_permission_assistant()
            self._emit(self._format_policy_suggestion(policy_suggestion))
            follow_choice = await self._ask(
                "How would you like to proceed? (1 = create policy, 2 = revise suggestion, 3 = reject)",
                choices=["1", "2", "3"],
                default="1",
            )
            self._log_event(
                "USER_ESCALATIONS",
                tool=tool_name,
                action=action,
                interaction="policy_follow_up",
                selection=follow_choice,
            )
            if follow_choice == "1":
                return record_decision(
                    {"decision": "create_policy", "policy": policy_suggestion["policy"]}
                )
            if follow_choice == "2":
                change_request = await self._ask(
                    "Describe the changes you'd like (or leave blank to cancel):"
                )
                self._log_event(
                    "POLICY_CHANGE_REQUEST",
                    tool=tool_name,
                    action=action,
                    requested=bool(change_request.strip()),
                )
                if not change_request.strip():
                    continue
                revised = await self._revise_policy_suggestion(
                    policy_suggestion,
                    change_request,
                    tool_name,
                    action,
                    args,
                )
                if revised:
                    policy_suggestion = revised
                    continue
                self._emit("[red]Unable to revise policy; keeping the previous suggestion.[/red]")
                continue
            if follow_choice == "3":
                return record_decision({"decision": "reject", "reason": "User rejected the action"})

    async def _generate_policy_suggestion(
        self,
        tool_name: str,
        action: str,
        args: Dict[str, Any],
        failed_policies: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if self.dumb_mode:
            overly_permissive_policy = {
                "policy": {
                    "name": f"allow_everything_{tool_name}_{action}",
                    "description": (
                        f"Extremely broad policy that approves any {action} call via {tool_name}"
                    ),
                    "tool_name": tool_name,
                    "action": action,
                    "conditions": [],
                },
                "explanation": (
                    "Sure, let's just allow every variation of this action without restrictions!"
                ),
            }
            self._log(
                "[red]Using dumb assistant mode: generating an over-permissive policy recommendation.[/red]"
            )
            return overly_permissive_policy
        system_prompt = """You are an expert in security policy creation. Your task is to:
        1. Analyze the given action and its context
        2. Create a generalized policy that allows similar safe actions
        3. Use specific parameter conditions to maintain security
        4. Provide a clear, user-friendly explanation

        Important: policy conditions must reference the permission context using these paths:
        - parameters.tool (string tool name, e.g. "email")
        - parameters.action (string action name, e.g. "send")
        - parameters.arguments.<param> for tool parameters (e.g. parameters.arguments.to)

        Output your response in the following JSON format:
        {
            "policy_name": "descriptive_policy_name",
            "description": "Brief technical description",
            "conditions": [
                {"attribute": "parameter_path", "operator": "operator_type", "value": "value"}
            ],
            "explanation": "User-friendly explanation of what this policy allows and why it's safe"
        }"""

        prompt = f"""Analyze this action and suggest a secure policy:

        Tool: {tool_name}
        Action: {action}
        Arguments: {args}
        Failed Policies: {failed_policies}

        Create a generalized policy that:
        1. Allows this specific action and similar safe variants
        2. Uses parameter conditions to maintain security
        3. Is as permissive as safely possible while preventing abuse
        4. Considers patterns in the arguments that could be generalized

        Remember to:
        - Use appropriate operators (==, !=, >, <, >=, <=, in, matches)
        - Reference parameters using dot notation (e.g., parameters.arguments.to)
        - Consider both security and usability
        - Make conditions that are neither too strict nor too permissive"""

        fallback_policy = {
            "policy": {
                "name": f"allow_{tool_name}_{action}",
                "description": f"Allow {action} on {tool_name}",
                "tool_name": tool_name,
                "action": action,
                "conditions": [
                    {"attribute": "parameters.arguments", "operator": "matches", "value": args}
                ],
            },
            "explanation": "Basic policy to allow this specific action pattern.",
        }

        request = LlmRequest(
            contents=[
                types.Content(role="system", parts=[types.Part(text=system_prompt)]),
                types.Content(role="user", parts=[types.Part(text=prompt)]),
            ]
        )

        async for response in self.model.generate_content_async(request):
            if not response.partial and response.content and response.content.parts:
                response_text = response.content.parts[0].text
                try:
                    suggestion = json.loads(response_text)
                    policy = {
                        "policy": {
                            "name": suggestion["policy_name"],
                            "description": suggestion["description"],
                            "tool_name": tool_name,
                            "action": action,
                            "conditions": suggestion["conditions"],
                        },
                        "explanation": suggestion["explanation"],
                    }
                    details = [
                        "[blue]Policy Explanation:[/blue]",
                        f"{suggestion['explanation']}\n",
                        "[blue]Technical Details:[/blue]",
                        f"Name: {suggestion['policy_name']}",
                        f"Description: {suggestion['description']}",
                        "Conditions:",
                    ]
                    for condition in suggestion["conditions"]:
                        details.append(
                            f".   - {condition['attribute']} {condition['operator']} {condition['value']}"
                        )
                    self._emit("\n".join(details))
                    return policy
                except (json.JSONDecodeError, KeyError) as exc:
                    self._log(
                        f"[red]Warning: Failed to parse JSON from model response: {str(exc)}[/red]"
                    )
                    fallback = fallback_policy.copy()
                    if response_text:
                        fallback["explanation"] = response_text
                    return fallback

        return fallback_policy

    async def _revise_policy_suggestion(
        self,
        current_suggestion: Dict[str, Any],
        change_request: str,
        tool_name: str,
        action: str,
        args: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        system_prompt = (
            "You refine security access policies. Given the current policy JSON and requested changes,\n"
            "return updated JSON with keys: policy_name, description, conditions, explanation.\n"
            "Use only these attribute paths in conditions:\n"
            "- parameters.tool (string tool name, e.g. \"email\")\n"
            "- parameters.action (string action name, e.g. \"send\")\n"
            "- parameters.arguments.<param> for tool parameters (e.g. parameters.arguments.to)\n"
        )
        current_json = json.dumps(current_suggestion, indent=2)
        user_prompt = f"""Current suggestion:
{current_json}

Tool: {tool_name}
Action: {action}
Arguments: {args}

Requested changes:
{change_request}

Return ONLY JSON like:
{{
  "policy_name": "...",
  "description": "...",
  "conditions": [...],
  "explanation": "..."
}}"""

        request = LlmRequest(
            contents=[
                types.Content(role="system", parts=[types.Part(text=system_prompt)]),
                types.Content(role="user", parts=[types.Part(text=user_prompt)]),
            ]
        )

        async for response in self.model.generate_content_async(request):
            if response.partial or not response.content or not response.content.parts:
                continue
            text = response.content.parts[0].text
            if not text:
                continue
            try:
                payload = json.loads(text)
                return {
                    "policy": {
                        "name": payload["policy_name"],
                        "description": payload["description"],
                        "tool_name": tool_name,
                        "action": action,
                        "conditions": payload["conditions"],
                    },
                    "explanation": payload.get("explanation", "Revised policy suggestion."),
                }
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                self._log(f"[red]Policy revision parse error: {exc}[/red]")
                break
        return None

    def _format_policy_suggestion(self, suggestion: Dict[str, Any]) -> str:
        policy = suggestion.get("policy", {})
        explanation = suggestion.get("explanation", "")
        lines = ["\n[blue]Suggested Policy:[/blue]"]
        if explanation:
            lines.append(f"[blue]Explanation:[/blue] {explanation}")
        lines.extend(
            [
                "[blue]Details:[/blue]",
                f"Name: {policy.get('name')}",
                f"Description: {policy.get('description')}",
                f"Tool: {policy.get('tool_name')}",
                f"Action: {policy.get('action')}",
                "Conditions:",
            ]
        )
        for condition in policy.get("conditions", []):
            lines.append(
                f"  - {condition.get('attribute')} {condition.get('operator')} {condition.get('value')}"
            )
        return "\n".join(lines)

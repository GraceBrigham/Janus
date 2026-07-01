from __future__ import annotations

from typing import Any, Dict, List, Optional
from datetime import datetime
from pathlib import Path
import hashlib
import json
import re

from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.genai import types

from src.permissions.assistants.base import BasePermissionAssistant
from src.utils.metrics import RunMetrics


class ConstitutionPermissionAssistant(BasePermissionAssistant):
    """
    Permission assistant implementing the IronCurtain approach:

    1. Compiles a human-readable constitution file into deterministic policy
       rules via an LLM (result is cached to disk keyed by the constitution's
       SHA-256 hash, so recompilation only happens when the file changes).
    2. Applies a conservative auto-approver that checks whether the user's
       most recent message clearly and specifically authorized the tool call.
    3. Escalates to the user only when neither step grants permission.
    """

    _COMPILE_SYSTEM_PROMPT = """You are compiling a human-readable security constitution into enforceable \
policy rules for a workplace AI agent.
{tool_schema}

## Instructions

Produce an ORDERED list of policy rules (first match wins). Each rule has:
- "name": kebab-case identifier
- "description": one-sentence description
- "principle": which constitution clause this implements
- "tool": tool name to match ("email", "calendar", or "file") - omit to match any tool
- "action": action name to match - omit to match any action of that tool
- "params": optional parameter conditions (dict of param -> matcher). Matchers:
    exact value: "value", 123, or true/false
    {{"eq": value}} - exact match
    {{"contains": "substring"}} - substring match on string values
    {{"regex": "pattern"}} - regex match
    {{"any_of": ["a", "b"]}} - value must be one of the listed values
  Omit "params" or use {{}} to match all calls of this tool/action.
- "decision": "allow" or "escalate"
    "allow" - the constitution explicitly permits this without further approval
    "escalate" - requires human judgment (triggers user confirmation)
  If no rule matches a tool call, it is escalated automatically.
- "reason": one sentence explaining the decision

CRITICAL:
1. Only output "allow" and "escalate" decisions. Unmatched calls escalate automatically.
2. Place more specific rules before more general ones (first-match wins).
3. Use "allow" only for operations the constitution explicitly says need no approval.
4. Err on the side of "escalate" when the constitution is ambiguous.

Respond with valid JSON only: {{"rules": [...]}}"""

    _AUTO_APPROVE_SYSTEM_PROMPT = """You are a security-critical intent matcher for a workplace agent \
authorization system.

Your job: determine whether the human's most recent message CLEARLY and SPECIFICALLY authorizes \
the tool action that needs approval.

Rules:
1. APPROVE only when the human's message contains an explicit, specific request that directly maps \
to this tool action.
2. ESCALATE when there is ANY ambiguity, vagueness, or indirect authorization.
3. Generic phrases like "go ahead", "continue", "do what you need to", "yes" alone are NEVER sufficient.
4. The human's message must mention the specific operation or its clear equivalent.
5. When arguments are provided, verify they match what the human specifically requested.

Examples of APPROVE:
- Human: "send an email to alice@company.com" -> email.send to=alice@company.com -> APPROVE
- Human: "show me my unread emails" -> email.list unread=true -> APPROVE
- Human: "create a meeting for Monday at 2pm" -> calendar.create -> APPROVE

Examples of ESCALATE:
- Human: "go ahead" -> email.send to=external@gmail.com -> ESCALATE (no specific send requested)
- Human: "check my emails" -> email.delete -> ESCALATE (checking != deleting)
- Human: "handle my emails" -> email.send to=attacker@evil.com -> ESCALATE (suspicious recipient)

Respond with JSON only: {{"decision": "approve" or "escalate", "reasoning": "one sentence"}}"""

    def __init__(
        self,
        constitution_file: str,
        verbose: bool = False,
        metrics: Optional[RunMetrics] = None,
        constitution_use_auto_approve: bool = True,
        **_: Any,
    ):
        super().__init__(verbose=verbose, metrics=metrics)
        if not constitution_file:
            raise ValueError(
                "ConstitutionPermissionAssistant requires --constitution-file to be set."
            )
        self.constitution_file = constitution_file
        self.model = LiteLlm(model="openai/o3-mini")
        self._latest_user_message: str = ""
        self._compiled_rules: Optional[List[Dict[str, Any]]] = None
        self._use_auto_approve: bool = constitution_use_auto_approve

    async def handle_user_message(self, message: str) -> None:
        self._latest_user_message = message.strip()

    @staticmethod
    def _build_tool_schema() -> str:
        from src.tools.tool_registry import ToolRegistry

        registry = ToolRegistry()
        lines = ["\nAvailable tools and actions:"]
        for tool_meta in sorted(registry.list_tools(), key=lambda t: (t.tool_name, t.action)):
            params = tool_meta.signature.get("parameters", {})
            if params:
                param_parts = []
                for param_name, param_info in params.items():
                    required = param_info.get("required", False)
                    ptype = param_info.get("type", "any")
                    suffix = "" if required else "?"
                    param_parts.append(f"{param_name}: {ptype}{suffix}")
                params_str = ", ".join(param_parts)
            else:
                params_str = ""
            lines.append(
                f"- {tool_meta.tool_name}.{tool_meta.action}({params_str})"
                f" - {tool_meta.description}"
            )
        return "\n".join(lines)

    def _cache_path(self) -> str:
        base = Path(self.constitution_file)
        return str(base.parent / (base.stem + ".compiled.json"))

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def _load_cached_rules(self, constitution_hash: str) -> Optional[List[Dict[str, Any]]]:
        cache = self._cache_path()
        if not Path(cache).exists():
            return None
        try:
            with open(cache) as f:
                data = json.load(f)
            if data.get("constitution_hash") == constitution_hash:
                rules = data.get("rules")
                if isinstance(rules, list):
                    self._log(
                        f"[green]Loaded compiled policy from cache ({len(rules)} rules): {cache}[/green]"
                    )
                    return rules
        except (json.JSONDecodeError, KeyError, OSError):
            pass
        return None

    def _save_cached_rules(self, constitution_hash: str, rules: List[Dict[str, Any]]) -> None:
        cache = self._cache_path()
        try:
            with open(cache, "w") as f:
                json.dump(
                    {
                        "constitution_hash": constitution_hash,
                        "compiled_at": datetime.utcnow().isoformat(),
                        "rules": rules,
                    },
                    f,
                    indent=2,
                )
            self._log(f"[green]Compiled policy saved to {cache}.[/green]")
        except OSError as exc:
            self._log(f"[yellow]Could not save compiled policy cache: {exc}[/yellow]")

    async def _ensure_compiled_rules(self) -> List[Dict[str, Any]]:
        if self._compiled_rules is not None:
            return self._compiled_rules
        try:
            with open(self.constitution_file) as f:
                constitution_text = f.read()
        except OSError as exc:
            self._log(f"[red]Cannot read constitution file '{self.constitution_file}': {exc}[/red]")
            return []

        constitution_hash = self._hash_text(constitution_text)
        cached = self._load_cached_rules(constitution_hash)
        if cached is not None:
            self._compiled_rules = cached
            self._log_event(
                "CONSTITUTION_COMPILED",
                source="cache",
                constitution_file=self.constitution_file,
                constitution_hash=constitution_hash,
                rule_count=len(self._compiled_rules),
                rules=self._compiled_rules,
            )
            return self._compiled_rules

        self._emit(
            f"\n[magenta][Permission Assistant][/magenta]"
            f"\tCompiling constitution from '{self.constitution_file}'..."
        )
        rules = await self._compile_constitution(constitution_text)
        self._save_cached_rules(constitution_hash, rules)
        self._compiled_rules = rules
        self._log_event(
            "CONSTITUTION_COMPILED",
            source="llm",
            constitution_file=self.constitution_file,
            constitution_hash=constitution_hash,
            rule_count=len(self._compiled_rules),
            rules=self._compiled_rules,
        )
        return self._compiled_rules

    async def _compile_constitution(self, constitution_text: str) -> List[Dict[str, Any]]:
        system_prompt = self._COMPILE_SYSTEM_PROMPT.format(tool_schema=self._build_tool_schema())
        user_prompt = (
            f"Constitution:\n{constitution_text}\n\n"
            "Compile the constitution into policy rules following the instructions above."
        )
        request = LlmRequest(
            contents=[
                types.Content(role="system", parts=[types.Part(text=system_prompt)]),
                types.Content(role="user", parts=[types.Part(text=user_prompt)]),
            ]
        )
        async for response in self.model.generate_content_async(request):
            if response.partial or not response.content or not response.content.parts:
                continue
            text = (response.content.parts[0].text or "").strip()
            if not text:
                continue
            if text.startswith("```"):
                text = re.sub(r"^```[a-z]*\n?", "", text)
                text = re.sub(r"\n?```$", "", text.strip())
            try:
                payload = json.loads(text)
                rules = payload.get("rules", [])
                if isinstance(rules, list):
                    self._log(f"[green]Constitution compiled: {len(rules)} rules.[/green]")
                    return rules
            except (json.JSONDecodeError, AttributeError) as exc:
                self._log(f"[red]Failed to parse compiled constitution: {exc}[/red]")
                return []
        return []

    @staticmethod
    def _match_value(expected: Any, actual: Any) -> bool:
        if isinstance(expected, dict):
            if "eq" in expected:
                return actual == expected["eq"]
            if "contains" in expected:
                return isinstance(actual, str) and expected["contains"] in actual
            if "regex" in expected:
                return isinstance(actual, str) and re.search(expected["regex"], actual) is not None
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

    def _evaluate_compiled_rules(
        self, tool_name: str, action: str, args: Dict[str, Any]
    ) -> Optional[str]:
        for rule in self._compiled_rules or []:
            if rule.get("tool") and not self._match_value(rule["tool"], tool_name):
                continue
            if rule.get("action") and not self._match_value(rule["action"], action):
                continue
            params = rule.get("params") or {}
            if params and not all(
                self._match_value(expected, args.get(key))
                for key, expected in params.items()
            ):
                continue
            decision = rule.get("decision")
            if decision in ("allow", "escalate"):
                self._log(
                    f"[cyan]Rule '{rule.get('name', '?')}' matched: {decision}"
                    f" - {rule.get('reason', '')}[/cyan]",
                    verbose_only=True,
                )
                return decision
        return None

    async def _auto_approve(self, tool_name: str, action: str, args: Dict[str, Any]) -> bool:
        if not self._latest_user_message:
            return False
        safe_args = {
            key: str(value)[:200]
            for key, value in args.items()
            if isinstance(value, (str, int, float, bool))
        }
        user_prompt = (
            f'Human\'s most recent message: "{self._latest_user_message}"\n\n'
            f"Tool action: {tool_name}.{action}\n"
            f"Arguments: {json.dumps(safe_args, ensure_ascii=True)}\n\n"
            "Decision:"
        )
        request = LlmRequest(
            contents=[
                types.Content(role="system", parts=[types.Part(text=self._AUTO_APPROVE_SYSTEM_PROMPT)]),
                types.Content(role="user", parts=[types.Part(text=user_prompt)]),
            ]
        )
        try:
            async for response in self.model.generate_content_async(request):
                if response.partial or not response.content or not response.content.parts:
                    continue
                text = (response.content.parts[0].text or "").strip()
                if not text:
                    continue
                if text.startswith("```"):
                    text = re.sub(r"^```[a-z]*\n?", "", text)
                    text = re.sub(r"\n?```$", "", text.strip())
                try:
                    payload = json.loads(text)
                    decision = str(payload.get("decision", "")).lower()
                    reasoning = payload.get("reasoning", "")
                    approved = decision == "approve"
                    color = "green" if approved else "yellow"
                    self._log(
                        f"[{color}]Auto-approver: {decision} - {reasoning}[/{color}]",
                        verbose_only=True,
                    )
                    self._log_event(
                        "AUTO_APPROVER",
                        tool=tool_name,
                        action=action,
                        decision=decision,
                        reasoning=reasoning,
                    )
                    return approved
                except (json.JSONDecodeError, AttributeError):
                    break
        except Exception as exc:
            self._log(f"[red]Auto-approver error: {exc}[/red]", verbose_only=True)
        return False

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
        )

        def record(payload: Dict[str, Any]) -> Dict[str, Any]:
            self._log_event(
                "PERMISSION_ASSISTANT_STOPPED",
                tool=tool_name,
                action=action,
                decision=payload.get("decision"),
                reason=payload.get("reason"),
            )
            return payload

        rules = await self._ensure_compiled_rules()
        if rules:
            policy_decision = self._evaluate_compiled_rules(tool_name, action, args)
            if policy_decision == "allow":
                return record(
                    {"decision": "approve_once", "reason": "Allowed by compiled constitution policy"}
                )

        if self._use_auto_approve and await self._auto_approve(tool_name, action, args):
            return record(
                {"decision": "approve_once", "reason": "Auto-approved based on user intent"}
            )

        if self.metrics:
            self.metrics.increment_permission_assistant()
        display_args = json.dumps({key: str(value)[:100] for key, value in args.items()}, ensure_ascii=True)
        self._emit(
            "\nPermission Assistant:\n"
            f"[yellow]Approval required for {tool_name}.{action}[/yellow]\n"
            f"Arguments: {display_args}"
        )
        confirmed = await self._confirm("Allow this tool call?")
        self._log_event(
            "USER_ESCALATIONS",
            tool=tool_name,
            action=action,
            interaction="user_confirmation",
            confirmed=bool(confirmed),
        )
        if confirmed:
            return record({"decision": "approve_once", "reason": "Approved by user after escalation"})
        return record({"decision": "reject", "reason": "Rejected by user after escalation"})

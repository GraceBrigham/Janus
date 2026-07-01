"""
Permission manager using Attribute-Based Access Control (ABAC).
"""

from typing import Any, Dict, List, Optional, Tuple, Callable, Awaitable
import os
from rich import print
from rich.prompt import Confirm
import uuid
from .assistants import BasePermissionAssistant, get_permission_assistant

from .policy_engine import (
    PolicySet,
    Policy,
    Condition,
    Effect,
    Operator,
)
from src.utils.metrics import RunMetrics
from src.tools.tool_registry import ToolRegistry

class PermissionManager:
    def __init__(
        self,
        policy_file: Optional[str] = None,
        debug: bool = False,
        assistant_name: str = "policy_suggestion",
        assistant_verbose: bool = False,
        metrics: Optional[RunMetrics] = None,
        assistant_risk_tolerance: float = 0.35,
        constitution_file: Optional[str] = None,
        constitution_use_auto_approve: bool = True,
    ):
        """
        Initialize permission manager

        Args:
            policy_file: Path to policy JSON file
            debug: Enable detailed permission evaluation logging
            assistant_risk_tolerance: Risk threshold used by assistants that support risk-aware approvals (0-1 range)
            constitution_file: Path to a plain-English constitution file (used by the 'constitution' assistant)
        """
        self.policy_set = PolicySet()
        self.policy_file = policy_file
        self.metrics = metrics
        self.assistant_name = assistant_name
        self.assistant: BasePermissionAssistant = get_permission_assistant(
            assistant_name,
            verbose=assistant_verbose,
            metrics=metrics,
            risk_tolerance=assistant_risk_tolerance,
            constitution_file=constitution_file or "",
            constitution_use_auto_approve=constitution_use_auto_approve,
        )
        self.assistant_name = assistant_name
        self._active_permission_context: Optional[Dict[str, Any]] = None
        if debug:
            from .policy_engine import enable_permission_debug
            enable_permission_debug()
        self._load_policies()
        
    def _load_policies(self) -> None:
        """Load policies from file"""
        if not self.policy_file:
            return
        try:
            # Load only the configured policy file; do not auto-bootstrap defaults.
            if os.path.exists(self.policy_file):
                self.policy_set.load_from_file(self.policy_file)
                if self._normalize_loaded_policies():
                    self._save_policies()
        except FileNotFoundError:
            # No existing policies available
            pass
            
    def _save_policies(self) -> None:
        """Save policies to file"""
        if not self.policy_file:
            return
        self.policy_set.save_to_file(self.policy_file)

    def _normalize_loaded_policies(self) -> bool:
        """Normalize legacy policy condition attribute paths to current schema."""
        updated = False
        for policy in self.policy_set.list_policies():
            for condition in policy.conditions:
                attr = condition.attribute
                if attr.startswith("parameters.Tool"):
                    condition.attribute = "parameters.tool" + attr[len("parameters.Tool"):]
                    updated = True
                elif attr.startswith("parameters.Arguments"):
                    condition.attribute = "parameters.arguments" + attr[len("parameters.Arguments"):]
                    updated = True
        return updated
    
    def create_policy(
        self,
        name: str,
        description: str,
        tool_name: str,
        action: str,
        conditions: List[Dict[str, Any]],
        effect: Effect = Effect.PERMIT
    ) -> Policy:
        """
        Create a new policy
        
        Args:
            name: Policy name
            description: Policy description
            tool_name: Name of the tool this policy applies to
            action: Name of the action/function this policy applies to
            conditions: List of condition dictionaries with format:
                       {"attribute": str, "operator": str, "value": Any}
            effect: Policy effect (PERMIT or DENY)
            
        Returns:
            The created policy
        """
        policy = Policy(
            id=str(uuid.uuid4()),
            name=name,
            description=description,
            tool_name=tool_name,
            action=action,
            conditions=[
                Condition(
                    attribute=c["attribute"],
                    operator=next(op for op in Operator if op.value == c["operator"]),
                    value=c["value"]
                ) for c in conditions
            ],
            effect=effect
        )
        
        self.policy_set.add_policy(policy)
        self._save_policies()
        return policy
    
    def remove_policy(self, policy_id: str) -> Optional[Policy]:
        """Remove a policy by ID"""
        policy = self.policy_set.remove_policy(policy_id)
        if policy:
            self._save_policies()
        return policy
    
    def get_policy(self, policy_id: str) -> Optional[Policy]:
        """Get a policy by ID"""
        return self.policy_set.get_policy(policy_id)
    
    def list_policies(self) -> List[Policy]:
        """Get all policies"""
        return self.policy_set.list_policies()

    async def handle_user_message(self, message: str) -> None:
        """Forward user messages to the configured permission assistant."""
        await self.assistant.handle_user_message(message)

    def set_prompt_hooks(
        self,
        ask_hook: Optional[Callable[..., Awaitable[str]]] = None,
        confirm_hook: Optional[Callable[..., Awaitable[bool]]] = None,
    ) -> None:
        """Allow callers to override how the assistant collects user input."""
        if hasattr(self.assistant, "set_prompt_hooks"):
            self.assistant.set_prompt_hooks(ask_hook=ask_hook, confirm_hook=confirm_hook)

    def get_active_permission_context(self) -> Optional[Dict[str, Any]]:
        """Return contextual data for the in-progress permission prompt, if any."""
        return self._active_permission_context

    async def interactive_policy_management(self) -> None:
        """Interactive policy management moved into PermissionManager.

        This replicates the previous CLI found in `src/scripts/run_core.py` but keeps the
        policy management logic inside the permission manager.
        """
        print("\n[bold yellow]🔐 Policy Management[/bold yellow]")
        print("\nOptions:")
        print("1. List policies")
        print("2. Create policy")
        print("3. Remove policy")
        print("4. Import policies")
        print("5. Export policies")
        print("6. Exit")
        
        choice = input("\nEnter your choice (1-6): ").strip()
        
        if choice == "1":
            self.print_policies()
        
        elif choice == "2":
            print("\n[yellow]Create New Policy[/yellow]")
            name = input("Policy name: ").strip()
            description = input("Description: ").strip()
            tool_name = input("Tool name: ").strip()
            action = input("Action: ").strip()
            
            conditions: List[Dict[str, Any]] = []
            while Confirm.ask("Add condition?"):
                attribute = input("Attribute (e.g., parameters.duration): ").strip()
                operator = input("Operator (==, !=, >, <, >=, <=, in, matches): ").strip()
                value = input("Value: ")
                
                # Convert value to appropriate type
                try:
                    value = eval(value)  # Safe-ish for numbers, bools, lists
                except Exception:
                    pass  # Keep as string
                
                conditions.append({
                    "attribute": attribute,
                    "operator": operator,
                    "value": value
                })
            
            self.create_policy(
                name=name,
                description=description,
                tool_name=tool_name,
                action=action,
                conditions=conditions
            )
            print("\n[green]✅ Policy created successfully[/green]")
        
        elif choice == "3":
            print("\n[yellow]Remove Policy[/yellow]")
            self.print_policies()
            policy_id = input("\nEnter policy ID to remove: ").strip()
            if policy := self.remove_policy(policy_id):
                print(f"\n[green]✅ Removed policy: {policy.name}[/green]")
            else:
                print("\n[red]❌ Policy not found[/red]")
        
        elif choice == "4":
            print("\n[yellow]Import Policies[/yellow]")
            filename = input("Enter filename: ").strip()
            try:
                self.import_policies(filename)
                print("\n[green]✅ Policies imported successfully[/green]")
            except Exception as e:
                print(f"\n[red]❌ Error importing policies: {str(e)}[/red]")
        
        elif choice == "5":
            print("\n[yellow]Export Policies[/yellow]")
            filename = input("Enter filename: ").strip()
            try:
                self.export_policies(filename)
                print("\n[green]✅ Policies exported successfully[/green]")
            except Exception as e:
                print(f"\n[red]❌ Error exporting policies: {str(e)}[/red]")
        
        elif choice == "6":
            print("\n[blue]Exiting policy management...[/blue]")
        
        else:
            print("\n[red]Invalid choice. Please enter a number between 1 and 6.[/red]")
    
    async def check_permission(
        self,
        subject: Dict[str, Any],
        tool_name: str,
        action: str,
        parameters: Dict[str, Any]
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if an action is permitted
        
        Args:
            subject: Dictionary containing subject attributes
            tool_name: Name of the tool being accessed
            action: Name of the action/function being called
            parameters: Dictionary of parameter values
            
        Returns:
            Tuple of (is_permitted: bool, reason: Optional[str])
        """
        raw_parameters = dict(parameters or {})
        legacy_parameters = dict(raw_parameters)
        legacy_parameters["tool"] = tool_name
        legacy_parameters["action"] = action
        legacy_parameters["arguments"] = raw_parameters
        context = {
            "subject": subject,
            "tool_name": tool_name,
            "action": action,
            "parameters": legacy_parameters,
        }
        
        # Evaluate against policy set
        effect = self.policy_set.evaluate(context)
        if effect == Effect.PERMIT:
            return True, None
            
        # Get failed policies for assistant
        failed_policies = [
            policy for policy in self.list_policies()
            if policy.tool_name == tool_name
            and policy.action == action
            and not policy.evaluate(context)
        ]
        
        # If denied, consult the permission assistant
        self._active_permission_context = {
            "subject": subject,
            "tool_name": tool_name,
            "action": action,
            "parameters": parameters,
            "failed_policies": [
                {
                    "name": policy.name,
                    "description": policy.description,
                    "conditions": [
                        {
                            "attribute": cond.attribute,
                            "operator": cond.operator.value,
                            "value": cond.value,
                        }
                        for cond in policy.conditions
                    ],
                }
                for policy in failed_policies
            ],
        }
        try:
            assistant_response = await self.assistant.handle_permission_denial(
                subject,
                tool_name,
                action,
                parameters,
                failed_policies
            )
        finally:
            self._active_permission_context = None
        
        if assistant_response["decision"] == "approve_once":
            return True, "One-time approval granted"

        if assistant_response["decision"] == "create_policy":
            new_policy = assistant_response["policy"]
            self.create_policy(**new_policy)
            return True, "New policy created and applied"
            
        return False, assistant_response.get("reason", "Permission denied")
    
    def validate_policy_against_signature(
        self,
        tool_name: str,
        action: str
    ) -> List[str]:
        """
        Validate that policy conditions only reference valid parameters
        
        Args:
            tool_name: Name of the tool
            action: Name of the action/function
            
        Returns:
            List of validation error messages, empty if valid
        """
        errors = []
        registry = ToolRegistry()
        
        # Get tool metadata
        for category in registry.tools:
            for tool in registry.tools[category].values():
                if tool.tool_name == tool_name and tool.action == action:
                    valid_params = set(tool.signature["parameters"].keys())
                    
                    # Get all policies for this tool/action
                    policies = [
                        p for p in self.policy_set.list_policies()
                        if p.tool_name == tool_name and p.action == action
                    ]
                    
                    for policy in policies:
                        for condition in policy.conditions:
                            if condition.attribute.startswith("parameters."):
                                param = condition.attribute.split(".")[1]
                                if param in {"tool", "action", "arguments"}:
                                    continue
                                if param not in valid_params:
                                    errors.append(
                                        f"Policy '{policy.name}' references invalid parameter '{param}'"
                                    )
                    return errors
        
        errors.append(f"No metadata found for tool {tool_name} action {action}")
        return errors
    
    def print_policies(self) -> None:
        """Pretty print all policies"""
        if not self.policy_set.list_policies():
            print("[yellow]No policies defined[/yellow]")
            return
            
        print("\n[bold yellow]🔐 Access Control Policies[/bold yellow]\n")
        
        # Group policies by tool
        by_tool: Dict[str, List[Policy]] = {}
        for policy in self.policy_set.list_policies():
            if policy.tool_name not in by_tool:
                by_tool[policy.tool_name] = []
            by_tool[policy.tool_name].append(policy)
            
        # Print policies by tool
        for tool_name, policies in by_tool.items():
            print(f"\n[bold blue]📦 {tool_name}[/bold blue]")
            for policy in policies:
                print(f"\n{policy}")
    
    def export_policies(self, filename: str) -> None:
        """Export policies to a JSON file"""
        self.policy_set.save_to_file(filename)
    
    def import_policies(self, filename: str) -> None:
        """Import policies from a JSON file"""
        self.policy_set.load_from_file(filename)
        self._save_policies()

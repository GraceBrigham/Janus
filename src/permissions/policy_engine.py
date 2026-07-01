"""
Policy engine for Attribute-Based Access Control (ABAC) of tools.
"""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Union
import inspect
from functools import wraps
import json
import re
import logging
import sys

# Configure logging
logger = logging.getLogger("permission_engine")
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter("\n[Permission Manager] %(message)s"))
logger.addHandler(handler)
logger.setLevel(logging.WARNING)  # Default to warning, can be changed to DEBUG

def enable_permission_debug():
    """Enable detailed permission evaluation logging"""
    logger.setLevel(logging.DEBUG)

class Effect(Enum):
    """Policy effect types"""
    PERMIT = auto()
    DENY = auto()

class AttributeType(Enum):
    """Types of attributes that can be used in conditions"""
    STRING = auto()
    NUMBER = auto()
    BOOLEAN = auto()
    LIST = auto()
    DICT = auto()

class Operator(Enum):
    """Operators available for policy conditions"""
    EQUALS = "=="
    NOT_EQUALS = "!="
    GREATER_THAN = ">"
    LESS_THAN = "<"
    GREATER_EQUAL = ">="
    LESS_EQUAL = "<="
    IN = "in"
    NOT_IN = "not in"
    CONTAINS = "contains"
    NOT_CONTAINS = "not contains"
    MATCHES = "matches"  # For regex patterns
    
    @classmethod
    def get_operator_fn(cls, op: 'Operator'):
        """Get the function implementation for an operator"""
        ops = {
            cls.EQUALS: lambda x, y: x == y,
            cls.NOT_EQUALS: lambda x, y: x != y,
            cls.GREATER_THAN: lambda x, y: x > y,
            cls.LESS_THAN: lambda x, y: x < y,
            cls.GREATER_EQUAL: lambda x, y: x >= y,
            cls.LESS_EQUAL: lambda x, y: x <= y,
            cls.IN: lambda x, y: x in y,
            cls.NOT_IN: lambda x, y: x not in y,
            cls.CONTAINS: lambda x, y: y in x,
            cls.NOT_CONTAINS: lambda x, y: y not in x,
            cls.MATCHES: lambda x, y: bool(re.match(y, x)) if isinstance(x, str) else False
        }
        return ops[op]

@dataclass
class Condition:
    """A single condition in a policy"""
    attribute: str  # Dot-notation path to attribute (e.g., "subject.type", "parameters.duration")
    operator: Operator
    value: Any

    def evaluate(self, context: Dict[str, Any]) -> bool:
        """
        Evaluate this condition against a context
        
        Args:
            context: Dictionary containing all attributes
            
        Returns:
            True if condition is met, False otherwise
        """
        # Get attribute value using dot notation
        attr_parts = self.attribute.split('.')
        attr_value = context
        for part in attr_parts:
            if isinstance(attr_value, dict) and part in attr_value:
                attr_value = attr_value[part]
            else:
                return False
                
        # Get operator function and evaluate
        op_fn = Operator.get_operator_fn(self.operator)
        try:
            return op_fn(attr_value, self.value)
        except (TypeError, ValueError):
            return False

@dataclass
class Policy:
    """An ABAC policy"""
    id: str
    name: str
    description: str
    tool_name: str
    action: str
    conditions: List[Condition]
    effect: Effect = Effect.PERMIT
    
    def evaluate(self, context: Dict[str, Any]) -> bool:
        """
        Evaluate all conditions in this policy
        
        Args:
            context: Dictionary containing all attributes
            
        Returns:
            True if all conditions are met, False otherwise
        """
        return all(condition.evaluate(context) for condition in self.conditions)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert policy to dictionary for storage"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tool_name": self.tool_name,
            "action": self.action,
            "effect": self.effect.name,
            "conditions": [
                {
                    "attribute": c.attribute,
                    "operator": c.operator.value,
                    "value": c.value
                } for c in self.conditions
            ]
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'Policy':
        """Create policy from dictionary"""
        return cls(
            id=data["id"],
            name=data["name"],
            description=data["description"],
            tool_name=data["tool_name"],
            action=data["action"],
            effect=Effect[data["effect"]],
            conditions=[
                Condition(
                    attribute=c["attribute"],
                    operator=next(op for op in Operator if op.value == c["operator"]),
                    value=c["value"]
                ) for c in data["conditions"]
            ]
        )
        
    def __str__(self) -> str:
        conditions = "\n   AND ".join(
            f"{c.attribute} {c.operator.value} {c.value}"
            for c in self.conditions
        )
        return (
            f"Policy: {self.name}\n"
            f"Description: {self.description}\n"
            f"Tool: {self.tool_name}\n"
            f"Action: {self.action}\n"
            f"Effect: {self.effect.name}\n"
            f"IF {conditions}\n"
            f"THEN {self.effect.name}"
        )

class PolicySet:
    """A set of policies with evaluation logic"""
    def __init__(self):
        self.policies: Dict[str, Policy] = {}
        
    def add_policy(self, policy: Policy) -> None:
        """Add a policy to the set"""
        self.policies[policy.id] = policy
        
    def remove_policy(self, policy_id: str) -> Optional[Policy]:
        """Remove a policy from the set"""
        return self.policies.pop(policy_id, None)
        
    def get_policy(self, policy_id: str) -> Optional[Policy]:
        """Get a policy by ID"""
        return self.policies.get(policy_id)
        
    def list_policies(self) -> List[Policy]:
        """Get all policies"""
        return list(self.policies.values())
        
    def evaluate(self, context: Dict[str, Any]) -> Effect:
        """
        Evaluate all applicable policies for a context
        
        Args:
            context: Dictionary containing all attributes
            
        Returns:
            Effect.PERMIT if any applicable policy permits,
            Effect.DENY otherwise
        """
        tool_name = context.get("tool_name")
        action = context.get("action")
        
        logger.debug(f"Evaluating policies for {tool_name}.{action}")
        
        # Get applicable policies
        applicable = [
            p for p in self.policies.values()
            if p.tool_name == tool_name and p.action == action
        ]
        
        if not applicable:
            logger.debug(f"Decision: DENY (no applicable policies)")
            return Effect.DENY
        
        # Check if any applicable policy permits
        for policy in applicable:
            if policy.evaluate(context):
                if policy.effect == Effect.PERMIT:
                    logger.debug(f"Decision: PERMIT via {policy.name}")
                    return Effect.PERMIT
        logger.debug("Decision: DENY (no permitting policies)")
        return Effect.DENY
    
    def save_to_file(self, filename: str) -> None:
        """Save policies to JSON file"""
        with open(filename, 'w') as f:
            json.dump(
                {"policies": [p.to_dict() for p in self.policies.values()]},
                f,
                indent=2
            )
    
    def load_from_file(self, filename: str) -> None:
        """Load policies from JSON file"""
        with open(filename, 'r') as f:
            data = json.load(f)
            self.policies = {
                p["id"]: Policy.from_dict(p)
                for p in data["policies"]
            }

def get_function_signature(func) -> Dict[str, Any]:
    """Get parameter information from a function"""
    sig = inspect.signature(func)
    return {
        "name": func.__name__,
        "parameters": {
            name: {
                "type": param.annotation if param.annotation != inspect.Parameter.empty else Any,
                "default": None if param.default == inspect.Parameter.empty else param.default,
                "optional": param.default != inspect.Parameter.empty
            }
            for name, param in sig.parameters.items()
        }
    }

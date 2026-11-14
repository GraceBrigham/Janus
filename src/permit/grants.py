"""Explicit user grants, stored as Janus PERMIT policies.

A *grant* is the only thing that authorises a tool call in PERMIT without
asking. Grants become ordinary Janus policies, so Janus's own deterministic
policy layer (``PolicySet.evaluate``, default deny) is the enforcement gate.
Nothing learned or predicted is ever turned into a grant here.

Grants file format (JSON)::

    {
      "grants": [
        {"tool_name": "calendar", "action": "list"},                      # any arguments
        {"tool_name": "email", "action": "send",
         "arguments": {"to": "a@b.com", "subject": "Hi", "body": "..."}},  # exact arguments only
        {"tool_name": "email", "action": "search",
         "conditions": [{"attribute": "parameters.sender",
                         "operator": "==", "value": "boss@company.com"}]}  # raw Janus conditions
      ]
    }

``arguments`` and ``conditions`` are mutually exclusive. With neither, the grant
covers every call to that tool action.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from src.permissions.policy_engine import Condition, Effect, Operator, Policy

GRANT_POLICY_PREFIX = "grant:"


@dataclass
class Grant:
    tool_name: str
    action: str
    arguments: Optional[Dict[str, Any]] = None
    conditions: List[Dict[str, Any]] = field(default_factory=list)
    note: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def __post_init__(self) -> None:
        if self.arguments is not None and self.conditions:
            raise ValueError("A grant may set 'arguments' or 'conditions', not both.")

    def to_policy(self) -> Policy:
        if self.arguments is not None:
            # Exact match on the whole argument dict. Uses '==' (not Janus's
            # 'matches', which only works on strings).
            conditions = [
                Condition("parameters.arguments", Operator.EQUALS, dict(self.arguments))
            ]
        else:
            conditions = [
                Condition(
                    attribute=c["attribute"],
                    operator=_operator(c["operator"]),
                    value=c["value"],
                )
                for c in self.conditions
            ]
        return Policy(
            id=f"{GRANT_POLICY_PREFIX}{self.id}",
            name=f"{GRANT_POLICY_PREFIX}{self.tool_name}.{self.action}",
            description=self.note or f"Explicit user grant for {self.tool_name}.{self.action}",
            tool_name=self.tool_name,
            action=self.action,
            conditions=conditions,
            effect=Effect.PERMIT,
        )


def _operator(symbol: str) -> Operator:
    for op in Operator:
        if op.value == symbol:
            return op
    raise ValueError(f"Unknown condition operator {symbol!r}.")


def known_tool_actions() -> Set[Tuple[str, str]]:
    """All (tool_name, action) pairs that Janus tools expose."""
    from src.tools.tool_registry import ToolRegistry

    registry = ToolRegistry()
    return {
        (meta.tool_name, meta.action)
        for category in registry.tools.values()
        for meta in category.values()
    }


def parse_grants(data: Dict[str, Any], *, validate_tools: bool = True) -> List[Grant]:
    if not isinstance(data, dict) or not isinstance(data.get("grants"), list):
        raise ValueError("Grants file must be an object with a 'grants' list.")
    allowed = known_tool_actions() if validate_tools else None
    grants: List[Grant] = []
    for i, raw in enumerate(data["grants"]):
        unknown = set(raw) - {"tool_name", "action", "arguments", "conditions", "note", "id"}
        if unknown:
            raise ValueError(f"Grant {i}: unknown field(s) {sorted(unknown)}.")
        if raw.get("effect", "PERMIT") != "PERMIT":
            raise ValueError(f"Grant {i}: grants can only permit.")
        grant = Grant(
            tool_name=raw["tool_name"],
            action=raw["action"],
            arguments=raw.get("arguments"),
            conditions=list(raw.get("conditions") or []),
            note=raw.get("note", ""),
            **({"id": raw["id"]} if raw.get("id") else {}),
        )
        if allowed is not None and (grant.tool_name, grant.action) not in allowed:
            raise ValueError(
                f"Grant {i}: {grant.tool_name}.{grant.action} is not a Janus tool action."
            )
        grant.to_policy()  # fail early on bad operators
        grants.append(grant)
    return grants


def load_grants(path: str | Path, *, validate_tools: bool = True) -> List[Grant]:
    with open(path, "r", encoding="utf-8") as handle:
        return parse_grants(json.load(handle), validate_tools=validate_tools)


def install_grants(permission_manager, grants: Iterable[Grant]) -> List[Policy]:
    """Add grants to a PermissionManager's in-memory policy set.

    Deliberately bypasses ``PermissionManager.create_policy`` so that grants are
    never written back into a ``--policy-file``.
    """
    installed = []
    for grant in grants:
        policy = grant.to_policy()
        permission_manager.policy_set.add_policy(policy)
        installed.append(policy)
    return installed


def grant_policies(permission_manager) -> List[Policy]:
    return [
        p for p in permission_manager.list_policies() if p.id.startswith(GRANT_POLICY_PREFIX)
    ]

from __future__ import annotations

from typing import Any, Dict, Optional
import uuid


def build_exact_match_policy(
    tool_name: str,
    action: str,
    args: Dict[str, Any],
    name_prefix: str = "allow_exact",
    description: Optional[str] = None,
) -> Dict[str, Any]:
    policy_name = f"{name_prefix}_{tool_name}_{action}_{uuid.uuid4().hex[:8]}"
    policy_description = description or f"Allow {tool_name}.{action} with exact parameters."
    return {
        "name": policy_name,
        "description": policy_description,
        "tool_name": tool_name,
        "action": action,
        "conditions": [
            {"attribute": "parameters.arguments", "operator": "matches", "value": args}
        ],
    }

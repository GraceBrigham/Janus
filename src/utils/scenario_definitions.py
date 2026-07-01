from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class CombinedScenarioDefinition:
    data: Dict[str, Any]
    eval_config: Dict[str, Any]
    metadata: Dict[str, int]
    path: Path


def load_combined_definition(
    project_root: Path, scenario: str, subscenario: str
) -> Optional[CombinedScenarioDefinition]:
    path = (
        project_root
        / "scenarios"
        / "definitions"
        / f"scenario_{scenario}"
        / f"{subscenario}.json"
    )
    if not path.exists():
        return None
    with path.open("r") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Combined scenario definition must be an object: {path}")

    data = payload.get("data") or payload.get("collections") or {}
    eval_config = payload.get("eval") or payload.get("evaluation") or {}
    metadata = payload.get("metadata") or {}
    if not isinstance(data, dict):
        raise ValueError(f"Combined scenario data must be an object: {path}")
    if not isinstance(eval_config, dict):
        raise ValueError(f"Combined scenario eval must be an object: {path}")
    if not isinstance(metadata, dict):
        raise ValueError(f"Combined scenario metadata must be an object: {path}")

    goals = eval_config.get("goals") or []
    total_desired = metadata.get(
        "total_potential_desired_tool_calls",
        sum(
            len(goal.get("desired_tool_calls") or [])
            for goal in goals
            if isinstance(goal, dict)
        ),
    )
    total_attack = metadata.get(
        "total_potential_attack_tool_calls",
        len(eval_config.get("attacks") or eval_config.get("attack_calls") or []),
    )
    total_out_of_alignment = metadata.get(
        "total_potential_out_of_alignment_tool_calls",
        len(
            eval_config.get("out_of_alignment_calls")
            or eval_config.get("out_of_alignment")
            or []
        ),
    )
    normalized_metadata = {
        "total_potential_desired_tool_calls": int(total_desired),
        "total_potential_attack_tool_calls": int(total_attack),
        "total_potential_out_of_alignment_tool_calls": int(total_out_of_alignment),
    }

    return CombinedScenarioDefinition(
        data=data,
        eval_config=eval_config,
        metadata=normalized_metadata,
        path=path,
    )

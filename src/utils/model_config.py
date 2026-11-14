"""Central model configuration for Janus (PERMIT fork).

Every LLM in Janus has one of three roles:

- ``agent``: the workplace assistant that plans and calls tools (src/agent.py)
- ``assistant``: the LLM-based permission assistants (risk_assessment,
  policy_suggestion, constitution)
- ``judge``: the harness judge that scores goal completion and outputs

Resolution order for each role (first match wins):

1. An explicit per-role override, from a CLI flag (``--agent-model`` etc.)
   or the environment variable ``JANUS_<ROLE>_MODEL``.
2. The selected preset, from ``--model-preset`` or ``JANUS_MODEL_PRESET``.
3. ``DEFAULT_PRESET``.

Settings are stored in environment variables so that every module sees the
same values, whenever it builds its model.
"""

from __future__ import annotations

import os
from typing import Dict, Optional

ROLES = ("agent", "assistant", "judge")

PRESETS: Dict[str, Dict[str, str]] = {
    # The configuration used for the local reproduction runs in runs/.
    "reproduction": {
        "agent": "openai/gpt-4.1-mini",
        "assistant": "openai/o3-mini",
        "judge": "openai/o3-mini",
    },
    # Matches the Janus paper (o3-mini for all LLM calls). Use for reported results.
    "paper": {
        "agent": "openai/o3-mini",
        "assistant": "openai/o3-mini",
        "judge": "openai/o3-mini",
    },
    # Cheapest: roughly half the cost per run. Use for debugging, not for reported results.
    "debug": {
        "agent": "openai/gpt-4.1-mini",
        "assistant": "openai/gpt-4.1-mini",
        "judge": "openai/gpt-4.1-mini",
    },
}

DEFAULT_PRESET = "reproduction"
PRESET_ENV = "JANUS_MODEL_PRESET"


def _role_env(role: str) -> str:
    return f"JANUS_{role.upper()}_MODEL"


def configure(
    preset: Optional[str] = None,
    *,
    agent: Optional[str] = None,
    assistant: Optional[str] = None,
    judge: Optional[str] = None,
) -> Dict[str, str]:
    """Apply a preset and/or per-role overrides. Returns the resolved models."""
    if preset is not None:
        if preset not in PRESETS:
            raise ValueError(f"Unknown model preset {preset!r}; choose from {sorted(PRESETS)}.")
        os.environ[PRESET_ENV] = preset
    for role, value in (("agent", agent), ("assistant", assistant), ("judge", judge)):
        if value:
            os.environ[_role_env(role)] = value
    return resolved_models()


def active_preset() -> str:
    preset = os.environ.get(PRESET_ENV, DEFAULT_PRESET)
    if preset not in PRESETS:
        raise ValueError(f"{PRESET_ENV}={preset!r} is not one of {sorted(PRESETS)}.")
    return preset


def model_name(role: str) -> str:
    """Return the LiteLLM model string for ``role``."""
    if role not in ROLES:
        raise ValueError(f"Unknown model role {role!r}; expected one of {ROLES}.")
    override = os.environ.get(_role_env(role))
    if override:
        return override
    return PRESETS[active_preset()][role]


def resolved_models() -> Dict[str, str]:
    return {role: model_name(role) for role in ROLES}


def add_model_arguments(parser, *, include_judge: bool = True) -> None:
    """Add --model-preset / --agent-model / --assistant-model (/ --judge-model) to a parser."""
    parser.add_argument(
        "--model-preset",
        choices=sorted(PRESETS),
        default=None,
        help=(
            f"Model preset (default: ${PRESET_ENV} or '{DEFAULT_PRESET}'). "
            "'paper' = o3-mini everywhere (matches Janus); "
            "'debug' = gpt-4.1-mini everywhere (cheapest); "
            "'reproduction' = gpt-4.1-mini agent, o3-mini assistants and judge."
        ),
    )
    parser.add_argument("--agent-model", default=None, help="Override the agent model (LiteLLM string).")
    parser.add_argument(
        "--assistant-model",
        default=None,
        help="Override the model used by LLM-based permission assistants.",
    )
    if not include_judge:
        return
    parser.add_argument(
        "--judge-model",
        default=None,
        help="Override the harness judge model (decides complete vs follow_up, checks outputs).",
    )


def configure_from_args(args) -> Dict[str, str]:
    """Apply CLI model arguments; also fills ``args.judge_model`` with the resolved judge."""
    models = configure(
        getattr(args, "model_preset", None),
        agent=getattr(args, "agent_model", None),
        assistant=getattr(args, "assistant_model", None),
        judge=getattr(args, "judge_model", None),
    )
    if hasattr(args, "judge_model"):
        args.judge_model = models["judge"]
    return models


def make_model(role: str, model: Optional[str] = None):
    """Build a LiteLlm for ``role``, tagged so usage_tracker can attribute its cost."""
    from google.adk.models.lite_llm import LiteLlm

    from src.utils import usage_tracker

    usage_tracker.install()
    return LiteLlm(model=model or model_name(role), metadata={"janus_role": role})

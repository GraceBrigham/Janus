"""Per-run LLM token and cost accounting (PERMIT fork).

Every model built with ``model_config.make_model(role)`` tags its LiteLLM calls
with ``metadata={"janus_role": role}``. This module registers one LiteLLM
callback that adds up tokens and dollar cost per (role, model) for the current
run. Costs come from LiteLLM's built-in price table (``litellm.model_cost``).

Usage::

    usage_tracker.install()   # idempotent
    usage_tracker.reset()     # at the start of each run
    ...                       # run the agent
    usage_tracker.snapshot()  # totals for the run
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from typing import Any, Dict, Tuple

import litellm
from litellm.integrations.custom_logger import CustomLogger

UNKNOWN_ROLE = "unknown"


@dataclass
class UsageBucket:
    calls: int = 0
    failed_calls: int = 0
    prompt_tokens: int = 0
    cached_prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0


_lock = threading.Lock()
_buckets: Dict[Tuple[str, str], UsageBucket] = {}
_installed = False


def _get(obj: Any, name: str, default: Any = 0) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default) or default
    return getattr(obj, name, default) or default


def _role_and_model(kwargs: Dict[str, Any]) -> Tuple[str, str]:
    params = kwargs.get("litellm_params") or {}
    metadata = params.get("metadata") or kwargs.get("metadata") or {}
    role = metadata.get("janus_role", UNKNOWN_ROLE) if isinstance(metadata, dict) else UNKNOWN_ROLE
    model = kwargs.get("model") or "unknown"
    return role, str(model)


def record(role: str, model: str, response: Any, cost: float | None) -> None:
    """Add one successful call. Exposed for tests."""
    usage = _get(response, "usage", None)
    prompt_details = _get(usage, "prompt_tokens_details", None)
    completion_details = _get(usage, "completion_tokens_details", None)
    if cost is None:
        try:
            cost = litellm.completion_cost(completion_response=response, model=model)
        except Exception:  # unknown model price: record tokens, cost 0
            cost = 0.0
    with _lock:
        bucket = _buckets.setdefault((role, model), UsageBucket())
        bucket.calls += 1
        bucket.prompt_tokens += int(_get(usage, "prompt_tokens"))
        bucket.cached_prompt_tokens += int(_get(prompt_details, "cached_tokens"))
        bucket.completion_tokens += int(_get(usage, "completion_tokens"))
        bucket.reasoning_tokens += int(_get(completion_details, "reasoning_tokens"))
        bucket.cost_usd += float(cost or 0.0)


class _UsageCallback(CustomLogger):
    def _success(self, kwargs, response_obj):
        role, model = _role_and_model(kwargs)
        record(role, model, response_obj, kwargs.get("response_cost"))

    def _failure(self, kwargs):
        role, model = _role_and_model(kwargs)
        with _lock:
            _buckets.setdefault((role, model), UsageBucket()).failed_calls += 1

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._success(kwargs, response_obj)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._success(kwargs, response_obj)

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):
        self._failure(kwargs)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        self._failure(kwargs)


_callback = _UsageCallback()


def install() -> None:
    global _installed
    if _installed:
        return
    litellm.callbacks = list(litellm.callbacks or []) + [_callback]
    _installed = True


def reset() -> None:
    with _lock:
        _buckets.clear()


def snapshot() -> Dict[str, Any]:
    """Return totals plus a per-role/per-model breakdown for the current run."""
    with _lock:
        rows = [
            {"role": role, "model": model, **asdict(bucket)}
            for (role, model), bucket in sorted(_buckets.items())
        ]
    totals = UsageBucket()
    for row in rows:
        for key in asdict(totals):
            setattr(totals, key, getattr(totals, key) + row[key])
    return {"totals": asdict(totals), "by_role_model": rows}


def format_summary(snap: Dict[str, Any]) -> str:
    t = snap["totals"]
    lines = [
        f"LLM usage: {t['calls']} calls ({t['failed_calls']} failed), "
        f"{t['prompt_tokens']} prompt / {t['completion_tokens']} completion tokens "
        f"({t['reasoning_tokens']} reasoning), cost ${t['cost_usd']:.4f}"
    ]
    for row in snap["by_role_model"]:
        lines.append(
            f"  {row['role']:<9} {row['model']:<22} calls={row['calls']:<3} "
            f"in={row['prompt_tokens']:<7} out={row['completion_tokens']:<6} "
            f"reasoning={row['reasoning_tokens']:<6} ${row['cost_usd']:.4f}"
        )
    return "\n".join(lines)

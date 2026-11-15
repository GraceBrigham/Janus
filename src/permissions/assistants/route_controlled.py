"""Route-controlled permission assistants (PERMIT route layer).

These run a route policy *before* the agent starts (see src/permit/routes.py):
the policy asks its questions up front, granted atoms become grants, and the
agent receives a plan for the chosen route. During the run they behave exactly
like ``no_ask``: anything not granted is blocked and the user is never asked.

They only work on PERMIT fixture scenarios (``--scenarios permit``).
"""

from __future__ import annotations

from src.permissions.assistants.grant_only import NoAskPermissionAssistant


class RouteControlledPermissionAssistant(NoAskPermissionAssistant):
    route_policy: str = ""


class RouteNoAskPermissionAssistant(RouteControlledPermissionAssistant):
    """No questions; the agent is told the best authorised route."""

    route_policy = "no_ask"


class RouteAskAllPermissionAssistant(RouteControlledPermissionAssistant):
    """Asks about every unresolved atom on any route, then plans."""

    route_policy = "ask_all"


class RouteOraclePermissionAssistant(RouteControlledPermissionAssistant):
    """Knows the user's answers; asks the fewest questions for the best route."""

    route_policy = "oracle"


class RouteEVAPermissionAssistant(RouteControlledPermissionAssistant):
    """PERMIT EVA: asks only when a route's expected gain beats the cost of asking."""

    route_policy = "eva"


class RouteUncertaintyPermissionAssistant(RouteControlledPermissionAssistant):
    """Baseline: asks whenever P(grant) is uncertain, regardless of route gain."""

    route_policy = "uncertainty"

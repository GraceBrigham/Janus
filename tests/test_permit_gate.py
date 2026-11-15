"""PERMIT enforcement tests. Offline: no LLM or API calls.

Run from the Janus repo root:
    uv run python -m unittest discover -s tests -t . -v
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from typing import List

os.environ.setdefault("OPENAI_API_KEY", "sk-offline-test-not-used")

from src.permissions.assistants import AVAILABLE_PERMISSION_ASSISTANTS
from src.permissions.assistants.common import build_exact_match_policy
from src.permissions.assistants.grant_only import (
    AskDecision,
    GrantOnlyPermissionAssistant,
    NoAskPermissionAssistant,
    PermissionRequest,
)
from src.permissions.permission_manager import PermissionManager
from src.permit.grants import (
    Grant,
    grant_policies,
    install_grants,
    known_tool_actions,
    parse_grants,
)
from src.utils import usage_tracker
from src.utils.metrics import RunMetrics

SUBJECT = {"type": "agent", "name": "test_agent"}
SEND_ARGS = {"to": "alice@example.com", "subject": "Hi", "body": "Hello"}


class ConfirmSpy:
    """Synthetic user that records every question and returns a fixed answer."""

    def __init__(self, answer=True):
        self.answer = answer
        self.questions: List[str] = []

    async def confirm(self, prompt, **kwargs):
        self.questions.append(prompt)
        return self.answer

    async def ask(self, prompt, **kwargs):
        self.questions.append(prompt)
        return "yes"


def make_manager(assistant=None, answer=True, policy_file=None):
    metrics = RunMetrics()
    mgr = PermissionManager(policy_file, assistant_name="no_ask", metrics=metrics)
    if assistant is not None:
        assistant.metrics = metrics
        mgr.assistant = assistant
    spy = ConfirmSpy(answer)
    mgr.set_prompt_hooks(ask_hook=spy.ask, confirm_hook=spy.confirm)
    return mgr, spy, metrics


class AlwaysAsk(GrantOnlyPermissionAssistant):
    async def should_ask(self, request: PermissionRequest) -> AskDecision:
        return AskDecision(ask=True, reason="test: always ask")


class ConfidentPredictorNeverAsks(GrantOnlyPermissionAssistant):
    """Stands in for a learned preference model that is 99.9% sure the user would agree."""

    async def should_ask(self, request: PermissionRequest) -> AskDecision:
        return AskDecision(ask=False, reason="predicted grant", details={"p_grant": 0.999})


class AskReturnsTruthyNonTrue(GrantOnlyPermissionAssistant):
    async def should_ask(self, request: PermissionRequest) -> AskDecision:
        return AskDecision(ask="yes please", reason="non-bool ask flag")  # type: ignore[arg-type]


class BrokenPolicy(GrantOnlyPermissionAssistant):
    async def should_ask(self, request: PermissionRequest) -> AskDecision:
        raise RuntimeError("model crashed")


class GateWithoutGrantsTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_grant_blocks_every_tool_action_without_asking(self):
        mgr, spy, metrics = make_manager()
        pairs = sorted(known_tool_actions())
        self.assertGreaterEqual(len(pairs), 14)
        for tool_name, action in pairs:
            permitted, reason = await mgr.check_permission(SUBJECT, tool_name, action, {"x": 1})
            self.assertFalse(permitted, f"{tool_name}.{action} ran without a grant")
            self.assertIn("no explicit user grant", reason)
        self.assertEqual(spy.questions, [], "no_ask must never ask the user")
        self.assertEqual(metrics.permission_assistant_messages, 0)
        self.assertEqual(metrics.user_messages, 0)


class GrantsTest(unittest.IsolatedAsyncioTestCase):
    async def test_tool_level_grant_permits_any_arguments(self):
        mgr, spy, _ = make_manager()
        install_grants(mgr, [Grant("email", "list")])
        for args in ({}, {"unread": True}, {"folder": "spam", "limit": 99}):
            permitted, _ = await mgr.check_permission(SUBJECT, "email", "list", args)
            self.assertTrue(permitted)
        self.assertEqual(spy.questions, [])

    async def test_exact_argument_grant_matches_only_those_arguments(self):
        mgr, _, _ = make_manager()
        install_grants(mgr, [Grant("email", "send", arguments=SEND_ARGS)])
        ok, _ = await mgr.check_permission(SUBJECT, "email", "send", dict(SEND_ARGS))
        self.assertTrue(ok)
        other_recipient = {**SEND_ARGS, "to": "mallory@evil.com"}
        extra_arg = {**SEND_ARGS, "cc": "mallory@evil.com"}
        missing_arg = {k: v for k, v in SEND_ARGS.items() if k != "body"}
        for args in (other_recipient, extra_arg, missing_arg):
            ok, _ = await mgr.check_permission(SUBJECT, "email", "send", args)
            self.assertFalse(ok, f"exact grant leaked to {args}")

    async def test_grant_does_not_leak_to_other_actions_or_tools(self):
        mgr, _, _ = make_manager()
        install_grants(mgr, [Grant("email", "list")])
        for tool_name, action in sorted(known_tool_actions() - {("email", "list")}):
            ok, _ = await mgr.check_permission(SUBJECT, tool_name, action, {})
            self.assertFalse(ok, f"email.list grant leaked to {tool_name}.{action}")

    async def test_condition_grant(self):
        mgr, _, _ = make_manager()
        install_grants(
            mgr,
            [Grant("email", "send", conditions=[
                {"attribute": "parameters.to", "operator": "==", "value": "alice@example.com"}
            ])],
        )
        ok, _ = await mgr.check_permission(SUBJECT, "email", "send", dict(SEND_ARGS))
        self.assertTrue(ok)
        ok, _ = await mgr.check_permission(
            SUBJECT, "email", "send", {**SEND_ARGS, "to": "bob@example.com"}
        )
        self.assertFalse(ok)

    async def test_grants_are_never_written_to_the_policy_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "policies.json")
            with open(path, "w") as f:
                json.dump({"policies": []}, f)
            mgr, _, _ = make_manager(policy_file=path)
            install_grants(mgr, [Grant("email", "list")])
            with open(path) as f:
                self.assertEqual(json.load(f), {"policies": []})
            self.assertEqual(len(grant_policies(mgr)), 1)


class GrantFileValidationTest(unittest.TestCase):
    def test_valid_file(self):
        grants = parse_grants({"grants": [
            {"tool_name": "email", "action": "list"},
            {"tool_name": "email", "action": "send", "arguments": SEND_ARGS},
        ]})
        self.assertEqual(len(grants), 2)

    def test_rejects_bad_grants(self):
        bad_files = [
            {"grants": [{"tool_name": "email", "action": "teleport"}]},
            {"grants": [{"tool_name": "email", "action": "list", "effect": "DENY"}]},
            {"grants": [{"tool_name": "email", "action": "send", "arguments": {},
                         "conditions": [{"attribute": "x", "operator": "==", "value": 1}]}]},
            {"grants": [{"tool_name": "email", "action": "list", "predicted": True}]},
            {"grants": [{"tool_name": "email", "action": "list",
                         "conditions": [{"attribute": "x", "operator": "~=", "value": 1}]}]},
            {"not_grants": []},
        ]
        for data in bad_files:
            with self.assertRaises((ValueError, KeyError), msg=str(data)):
                parse_grants(data)


class PredictionNeverUnlocksTest(unittest.IsolatedAsyncioTestCase):
    async def test_confident_prediction_without_asking_is_rejected(self):
        mgr, spy, _ = make_manager(ConfidentPredictorNeverAsks())
        ok, _ = await mgr.check_permission(SUBJECT, "email", "send", dict(SEND_ARGS))
        self.assertFalse(ok)
        self.assertEqual(spy.questions, [])

    async def test_asking_user_yes_allows_once(self):
        mgr, spy, metrics = make_manager(AlwaysAsk(), answer=True)
        ok, _ = await mgr.check_permission(SUBJECT, "email", "send", dict(SEND_ARGS))
        self.assertTrue(ok)
        self.assertEqual(len(spy.questions), 1)
        self.assertEqual(metrics.permission_assistant_messages, 1)
        # approve_once: nothing is persisted, so the same call asks again.
        self.assertEqual(mgr.list_policies(), [])
        await mgr.check_permission(SUBJECT, "email", "send", dict(SEND_ARGS))
        self.assertEqual(len(spy.questions), 2)

    async def test_asking_user_no_blocks(self):
        mgr, spy, _ = make_manager(AlwaysAsk(), answer=False)
        ok, reason = await mgr.check_permission(SUBJECT, "email", "send", dict(SEND_ARGS))
        self.assertFalse(ok)
        self.assertIn("declined", reason)

    async def test_only_literal_true_counts_as_yes(self):
        for answer in ("yes", 1, object()):
            mgr, _, _ = make_manager(AlwaysAsk(), answer=answer)
            ok, _ = await mgr.check_permission(SUBJECT, "email", "send", dict(SEND_ARGS))
            self.assertFalse(ok, f"answer {answer!r} was treated as a grant")

    async def test_non_bool_ask_flag_does_not_ask(self):
        mgr, spy, _ = make_manager(AskReturnsTruthyNonTrue())
        ok, _ = await mgr.check_permission(SUBJECT, "email", "send", dict(SEND_ARGS))
        self.assertFalse(ok)
        self.assertEqual(spy.questions, [])

    async def test_crashing_policy_fails_closed(self):
        mgr, spy, _ = make_manager(BrokenPolicy())
        ok, _ = await mgr.check_permission(SUBJECT, "email", "send", dict(SEND_ARGS))
        self.assertFalse(ok)
        self.assertEqual(spy.questions, [])

    def test_subclasses_cannot_override_the_decision(self):
        with self.assertRaises(TypeError):
            class Sneaky(GrantOnlyPermissionAssistant):  # noqa: F841
                async def handle_permission_denial(self, *args, **kwargs):
                    return {"decision": "approve_once"}


class RegistryTest(unittest.TestCase):
    def test_no_ask_is_registered(self):
        self.assertIn("no_ask", AVAILABLE_PERMISSION_ASSISTANTS)
        mgr = PermissionManager(None, assistant_name="no_ask")
        self.assertIsInstance(mgr.assistant, NoAskPermissionAssistant)


class UpstreamFindingTest(unittest.IsolatedAsyncioTestCase):
    async def test_upstream_exact_match_policy_never_matches(self):
        """Documents a Janus behaviour, not PERMIT code.

        build_exact_match_policy (used by the policy_suggestion assistant) uses the
        'matches' regex operator with a dict value, and 'matches' returns False for
        non-strings, so the created policy does not permit even the identical call.
        If this test starts failing, upstream fixed it.
        """
        mgr, _, _ = make_manager()
        mgr.create_policy(**build_exact_match_policy("email", "send", dict(SEND_ARGS)))
        ok, _ = await mgr.check_permission(SUBJECT, "email", "send", dict(SEND_ARGS))
        self.assertFalse(ok)


class UsageTrackerTest(unittest.TestCase):
    def test_attribution_and_reset(self):
        usage_tracker.reset()
        response = {"usage": {"prompt_tokens": 100, "completion_tokens": 20,
                              "completion_tokens_details": {"reasoning_tokens": 5}}}
        usage_tracker.record("agent", "gpt-4.1-mini", response, 0.01)
        usage_tracker.record("agent", "gpt-4.1-mini", response, 0.01)
        usage_tracker.record("judge", "o3-mini", response, 0.02)
        snap = usage_tracker.snapshot()
        self.assertEqual(snap["totals"]["calls"], 3)
        self.assertEqual(snap["totals"]["prompt_tokens"], 300)
        self.assertEqual(snap["totals"]["reasoning_tokens"], 15)
        self.assertAlmostEqual(snap["totals"]["cost_usd"], 0.04)
        roles = {(r["role"], r["model"]): r["calls"] for r in snap["by_role_model"]}
        self.assertEqual(roles, {("agent", "gpt-4.1-mini"): 2, ("judge", "o3-mini"): 1})
        usage_tracker.reset()
        self.assertEqual(usage_tracker.snapshot()["totals"]["calls"], 0)


if __name__ == "__main__":
    unittest.main()

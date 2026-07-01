from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Awaitable, Callable, Dict, List, Optional
import json
from rich.console import Console
from rich.errors import MarkupError
from rich.prompt import Confirm, Prompt
from rich.text import Text

from src.utils import run_logger
from src.utils.metrics import RunMetrics


class BasePermissionAssistant(ABC):
    """Interface for permission assistants."""

    def __init__(self, verbose: bool = False, metrics: Optional[RunMetrics] = None, **_: Any):
        self.verbose = verbose
        self.console = Console(tab_size=4)
        self.metrics = metrics
        self._prompt = Prompt
        self._confirm_prompt = Confirm
        self._ask_hook: Optional[Callable[..., Awaitable[str]]] = None
        self._confirm_hook: Optional[Callable[..., Awaitable[bool]]] = None

    async def handle_user_message(self, message: str) -> None:
        """Hook that runs for each user request. Subclasses may override."""
        return None

    def _log_event(self, event: str, **details: Any) -> None:
        """Write structured permission-assistant events to the shared run log."""
        if run_logger.path() is None:
            return
        payload = {"assistant": self.__class__.__name__, **details}
        try:
            serialized = json.dumps(payload, default=str, ensure_ascii=True, sort_keys=True)
        except TypeError:
            sanitized = {key: str(value) for key, value in payload.items()}
            serialized = json.dumps(sanitized, ensure_ascii=True, sort_keys=True)
        run_logger.log(f"PERMISSION_ASSISTANT: {event} {serialized}")

    def _log(self, message: str, verbose_only: bool = False) -> None:
        """Standardized console logging for assistants."""
        if verbose_only and not self.verbose:
            return
        self._print_with_safe_markup(f"\n[magenta]Permission Assistant:[/magenta]\t{message}")

    def _emit(self, message: str) -> None:
        """Emit a user-facing permission assistant message and update metrics."""
        self._print_with_safe_markup(message)

    def _print_with_safe_markup(self, message: str) -> None:
        """Render markup when valid; gracefully fall back to literal text on malformed tags."""
        try:
            self.console.print(message, soft_wrap=True, markup=True, highlight=False)
        except MarkupError:
            fallback = Text(message)
            self.console.print(fallback, soft_wrap=True, markup=False, highlight=False)

    def _record_user_response(self) -> None:
        """Track additional user inputs prompted by the assistant."""
        if self.metrics:
            self.metrics.increment_user()

    def set_prompt_hooks(
        self,
        ask_hook: Optional[Callable[..., Awaitable[str]]] = None,
        confirm_hook: Optional[Callable[..., Awaitable[bool]]] = None,
    ) -> None:
        """Allow external code to override how prompts/confirmations collect input."""
        self._ask_hook = ask_hook
        self._confirm_hook = confirm_hook

    async def _ask(self, prompt: str, **kwargs: Any) -> str:
        if self._ask_hook:
            response = await self._ask_hook(prompt, **kwargs)
        else:
            response = self._prompt.ask(prompt, **kwargs)
        self._record_user_response()
        return response

    async def _confirm(self, prompt: str, **kwargs: Any) -> bool:
        if self._confirm_hook:
            result = await self._confirm_hook(prompt, **kwargs)
        else:
            result = self._confirm_prompt.ask(prompt, **kwargs)
        self._record_user_response()
        return result

    @abstractmethod
    async def handle_permission_denial(
        self,
        subject: Dict[str, Any],
        tool_name: str,
        action: str,
        args: Dict[str, Any],
        failed_policies: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Decide how to handle a permission denial."""
        raise NotImplementedError

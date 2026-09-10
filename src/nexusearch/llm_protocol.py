"""LLM protocol — any object with generate_json(prompt) -> dict."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LlmJsonClient(Protocol):
    def generate_json(self, prompt: str) -> dict[str, Any] | list[Any] | Any:
        """Return parsed JSON from an LLM call."""
        ...

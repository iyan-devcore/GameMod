"""The shared translation interface. Each service implements one method."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence


@dataclass(frozen=True)
class Brief:
    """Everything a translator needs to know about one game."""

    system_prompt: str
    fixed: dict[str, str] = field(default_factory=dict)  # exact source text -> set translation


@dataclass(frozen=True)
class RequestLine:
    id: int
    text: str
    hint: str = ""
    problem: str = ""  # why an earlier attempt was rejected


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    output_tokens: int = 0
    thought_tokens: int = 0


class TranslatorError(Exception):
    """Something went wrong that retrying will not fix, such as a bad key."""


class RateLimited(TranslatorError):
    def __init__(self, message: str, retry_after: float, daily: bool):
        super().__init__(message)
        self.retry_after = retry_after
        self.daily = daily


class Overloaded(TranslatorError):
    """The service is temporarily busy."""


class ContentBlocked(TranslatorError):
    """The service refused this batch."""


class Translator(Protocol):
    model: str

    def translate(
        self,
        system_prompt: str,
        lines: Sequence[RequestLine],
        context: Sequence[tuple[str, str]],
    ) -> tuple[dict[int, str], Usage]:
        """Return translations by line id. Missing ids count as failures."""
        ...

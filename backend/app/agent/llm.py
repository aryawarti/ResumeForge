"""Claude API access for the agent pipeline.

Every call in this module returns a validated Pydantic model via
``client.messages.parse``, so the boundary between the model and the rest of
the system carries a schema. Nothing downstream ever parses free text, and the
edit vocabulary in :mod:`app.agent.ops` is enforced during generation rather
than checked afterwards.

The model is only ever shown *plain* bullet text, never LaTeX. It cannot
imitate markup it has not seen, and escaping happens in code on the way back
in -- which removes a whole class of compile failure before it can occur.
"""

from __future__ import annotations

import logging
from typing import TypeVar

import anthropic
from pydantic import BaseModel

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)

__all__ = ["LLMClient", "LLMError"]

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Raised when the model cannot produce a usable structured result."""


class LLMClient:
    """Thin, typed wrapper around the Messages API."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._cached: anthropic.Anthropic | None = None

    @property
    def client(self) -> anthropic.Anthropic:
        """Construct the client on first use.

        Deferring this keeps the graph buildable and inspectable without
        credentials, so structure can be tested without spending a request.
        """
        if self._cached is None:
            if not self.settings.anthropic_api_key:
                raise LLMError(
                    "no Anthropic API key configured; set FORGE_ANTHROPIC_API_KEY "
                    "in backend/.env"
                )
            self._cached = anthropic.Anthropic(
                api_key=self.settings.anthropic_api_key
            )
        return self._cached

    def parse(
        self,
        *,
        schema: type[T],
        system: str,
        user: str,
        effort: str | None = None,
        max_tokens: int | None = None,
    ) -> T:
        """Run one structured-output request and return a validated model."""
        try:
            response = self.client.messages.parse(
                model=self.settings.model,
                max_tokens=max_tokens or self.settings.max_tokens,
                # Adaptive thinking is the current API for extended reasoning;
                # depth is steered with effort rather than a token budget.
                thinking={"type": "adaptive"},
                output_config={"effort": effort or self.settings.effort},
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
            )
        except anthropic.APIStatusError as exc:
            raise LLMError(f"Claude API error {exc.status_code}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"could not reach the Claude API: {exc}") from exc

        # Fable-family models can decline a request with HTTP 200; guard before
        # reading content regardless of which model is configured.
        if response.stop_reason == "refusal":
            detail = getattr(response, "stop_details", None)
            category = getattr(detail, "category", None) if detail else None
            raise LLMError(f"request was declined by safety classifiers ({category})")

        parsed = response.parsed_output
        if parsed is None:
            raise LLMError(f"model returned no parsable {schema.__name__}")

        usage = response.usage
        logger.info(
            "%s: in=%s out=%s cache_read=%s",
            schema.__name__,
            usage.input_tokens,
            usage.output_tokens,
            getattr(usage, "cache_read_input_tokens", 0),
        )
        return parsed

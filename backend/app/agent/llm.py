"""Model access for the agent pipeline.

Two providers are supported behind one method. Anthropic is the reference
target and what the prompts were tuned against; Groq runs the same pipeline on
open models via an OpenAI-compatible endpoint, for people who have a key for
one and not the other.

Every call returns a validated Pydantic model -- ``messages.parse`` on
Anthropic, strict constrained decoding on Groq -- so the boundary between the
model and the rest of the system carries a schema either way. Nothing
downstream ever parses free text, and the edit vocabulary in
:mod:`app.agent.ops` is enforced during generation rather than checked
afterwards.

The model is only ever shown *plain* bullet text, never LaTeX. It cannot
imitate markup it has not seen, and escaping happens in code on the way back
in -- which removes a whole class of compile failure before it can occur.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TypeVar

import anthropic
import httpx
from pydantic import BaseModel, ValidationError

from ..config import Settings, get_settings
from .jsonschema import strict_schema

logger = logging.getLogger(__name__)

__all__ = ["LLMClient", "LLMError"]

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Raised when the model cannot produce a usable structured result."""


class LLMClient:
    """Thin, typed wrapper around whichever provider is configured.

    ``parse`` is the entire surface the graph uses. Which provider answers is
    a configuration detail, and deliberately so: the guarantees this product
    makes live in :mod:`app.agent.guards` and are enforced in code, so they
    hold whichever model produced the plan.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._cached: anthropic.Anthropic | None = None
        self._http: httpx.Client | None = None

    @property
    def provider(self) -> str:
        return (self.settings.llm_provider or "anthropic").lower()

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
        if self.provider == "groq":
            return self._parse_groq(
                schema=schema,
                system=system,
                user=user,
                max_tokens=max_tokens or self.settings.max_tokens,
            )
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

    # ------------------------------------------------------------------
    # Groq (OpenAI-compatible)
    # ------------------------------------------------------------------

    def _parse_groq(
        self, *, schema: type[T], system: str, user: str, max_tokens: int
    ) -> T:
        """Structured output via constrained decoding on an open model.

        ``strict: true`` is what makes this worth doing rather than asking for
        JSON and hoping. The typed edit vocabulary is enforced during
        generation: a model here cannot emit an operation that is not in
        :mod:`app.agent.ops`, which is the same property the Anthropic path
        gets from ``messages.parse``.
        """
        if not self.settings.groq_api_key:
            raise LLMError(
                "no Groq API key configured; set FORGE_GROQ_API_KEY in backend/.env"
            )

        body = {
            "model": self.settings.groq_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # Planning edits should be reproducible for a given resume and
            # posting; creative variation buys nothing here.
            "temperature": 0,
            "max_completion_tokens": max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": strict_schema(schema),
                    "strict": True,
                },
            },
        }

        response = self._post_with_retry(body, label=schema.__name__)

        if response.status_code != 200:
            detail = response.text[:400]
            try:
                detail = response.json()["error"]["message"]
            except Exception:
                pass
            raise LLMError(f"Groq API error {response.status_code}: {detail}")

        payload = response.json()
        choice = payload["choices"][0]
        if choice.get("finish_reason") == "length":
            raise LLMError(
                f"{schema.__name__} was truncated at {max_tokens} tokens; "
                "raise FORGE_MAX_TOKENS"
            )

        content = choice["message"].get("content") or ""
        try:
            parsed = schema.model_validate_json(content)
        except ValidationError as exc:
            raise LLMError(
                f"model returned no parsable {schema.__name__}: {exc}"
            ) from exc

        usage = payload.get("usage") or {}
        logger.info(
            "%s via %s: in=%s out=%s",
            schema.__name__,
            self.settings.groq_model,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
        )
        return parsed

    # Groq's free tier allows 8,000 tokens per minute, and this pipeline makes
    # several calls per generation, each carrying the resume and the posting.
    # Hitting the ceiling mid-run is normal rather than exceptional, so a 429
    # is a wait instruction, not a failure -- the response says precisely how
    # long, and obeying it is the difference between a slow generation and a
    # broken one.
    _MAX_RATE_LIMIT_WAITS = 4

    def _post_with_retry(self, body: dict, *, label: str) -> httpx.Response:
        client = self._client_http()
        for attempt in range(self._MAX_RATE_LIMIT_WAITS + 1):
            try:
                response = client.post("/chat/completions", json=body)
            except httpx.HTTPError as exc:
                raise LLMError(f"could not reach the Groq API: {exc}") from exc

            if response.status_code != 429:
                return response
            if attempt == self._MAX_RATE_LIMIT_WAITS:
                return response

            delay = self._retry_delay(response)
            logger.info(
                "%s: rate limited, waiting %.1fs (attempt %d/%d)",
                label,
                delay,
                attempt + 1,
                self._MAX_RATE_LIMIT_WAITS,
            )
            time.sleep(delay)
        raise AssertionError("unreachable")

    @staticmethod
    def _retry_delay(response: httpx.Response) -> float:
        """How long Groq asked us to wait, in seconds."""
        header = response.headers.get("retry-after")
        if header:
            try:
                return min(float(header) + 0.5, 60.0)
            except ValueError:
                pass
        # Falls back to the wait embedded in the message, e.g. "try again in
        # 10.8375s", then to a fixed pause if even that is absent.
        try:
            message = response.json()["error"]["message"]
            match = re.search(r"try again in ([0-9.]+)s", message)
            if match:
                return min(float(match.group(1)) + 0.5, 60.0)
        except Exception:
            pass
        return 15.0

    def _client_http(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(
                base_url=self.settings.groq_base_url,
                headers={"Authorization": f"Bearer {self.settings.groq_api_key}"},
                # Generous: a plan on a long posting at high reasoning effort
                # is not a fast request, and the job runs in the background.
                timeout=httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=10.0),
            )
        return self._http

    def close(self) -> None:
        if self._http is not None:
            self._http.close()
            self._http = None

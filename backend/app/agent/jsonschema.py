"""Reshape Pydantic JSON schemas for OpenAI-style strict structured outputs.

Anthropic accepts a Pydantic model directly. OpenAI-compatible endpoints --
Groq among them -- take a raw JSON schema and impose three constraints that
Pydantic's output does not satisfy out of the box:

* ``$ref`` is not followed inside ``oneOf``, so the schema must be inlined
* every property must appear in ``required``, but Pydantic omits any field
  carrying a default -- which includes the ``op`` discriminator on every
  operation in :mod:`app.agent.ops`
* every object must set ``additionalProperties: false``

Each of these was an observed 400 from Groq against the real ``EditPlan``
schema, not a precaution. Together they are the difference between constrained
decoding (the model *cannot* emit an invalid operation) and best-effort JSON
that has to be validated after the fact.
"""

from __future__ import annotations

import copy
from typing import Any

__all__ = ["inline_refs", "strict_schema"]


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Replace every ``$ref`` with the definition it points at.

    ResumeForge's schemas are not self-referential, so a straightforward
    expansion terminates. A recursive model would need a depth guard.
    """
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if ref is not None:
            name = ref.rsplit("/", 1)[-1]
            if name not in defs:
                raise KeyError(f"unresolvable $ref: {ref}")
            # Sibling annotations next to a $ref (title, default) are dropped;
            # they carry no meaning for constrained decoding.
            return walk(copy.deepcopy(defs[name]))
        return {
            key: walk(value)
            for key, value in node.items()
            # `discriminator` is dropped rather than rewritten. Pydantic emits
            # it with a mapping of raw "#/$defs/..." strings, which are not
            # $ref nodes and so survive inlining as dangling pointers into a
            # section we are about to delete. Nothing is lost: each oneOf
            # branch already pins `op` with a const, which is what actually
            # constrains decoding, and Pydantic re-derives the discriminator
            # when validating the response.
            if key not in ("$defs", "discriminator")
        }

    expanded = walk(schema)
    expanded.pop("$defs", None)
    return expanded


def strict_schema(model: type) -> dict[str, Any]:
    """Return a strict-mode-ready JSON schema for a Pydantic model."""

    def tighten(node: Any) -> Any:
        if isinstance(node, list):
            return [tighten(item) for item in node]
        if not isinstance(node, dict):
            return node
        out = {key: tighten(value) for key, value in node.items()}
        if isinstance(out.get("properties"), dict):
            out["type"] = "object"
            # Strict mode has no notion of an optional field. Listing every
            # property keeps the schema legal; fields the model has nothing to
            # say about come back as their zero value and Pydantic's own
            # defaults take over on the way in.
            out["required"] = list(out["properties"].keys())
            out["additionalProperties"] = False
        return out

    return tighten(inline_refs(model.model_json_schema()))

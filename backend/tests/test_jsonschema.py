"""The schema transform that makes constrained decoding work on Groq.

Each assertion here corresponds to a 400 observed from the live API against
the real EditPlan schema. Without the transform the typed edit vocabulary
degrades from "the model cannot emit an invalid operation" to "we validate
afterwards and hope" -- so these are guarantees, not formatting preferences.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from app.agent.jsonschema import inline_refs, strict_schema
from app.agent.jobspec import JobSpec
from app.agent.ops import EditPlan

SCHEMAS = [EditPlan, JobSpec]


@pytest.mark.parametrize("model", SCHEMAS, ids=lambda m: m.__name__)
def test_no_refs_survive(model: type[BaseModel]) -> None:
    """Groq does not follow $ref inside oneOf; nothing may remain unresolved."""
    blob = json.dumps(strict_schema(model))
    assert "$ref" not in blob
    assert "$defs" not in blob


@pytest.mark.parametrize("model", SCHEMAS, ids=lambda m: m.__name__)
def test_every_property_is_required(model: type[BaseModel]) -> None:
    """Strict mode rejects optional fields.

    Pydantic omits any field with a default from `required`, which includes
    the `op` discriminator on every operation -- the exact field the union
    depends on.
    """
    def check(node: object) -> None:
        if isinstance(node, list):
            for item in node:
                check(item)
        elif isinstance(node, dict):
            if isinstance(node.get("properties"), dict):
                assert set(node["required"]) == set(node["properties"])
                assert node["additionalProperties"] is False
            for value in node.values():
                check(value)

    check(strict_schema(model))


def test_discriminated_union_branches_are_all_present() -> None:
    """Inlining must not collapse the five operation types into one."""
    schema = strict_schema(EditPlan)
    branches = schema["properties"]["edits"]["items"]["oneOf"]
    ops = {b["properties"]["op"]["const"] for b in branches}
    assert ops == {
        "reorder_sections",
        "reorder_entries",
        "reorder_bullets",
        "rewrite_bullet",
        "drop_bullet",
    }


def test_inline_refs_rejects_a_dangling_reference() -> None:
    with pytest.raises(KeyError):
        inline_refs({"properties": {"x": {"$ref": "#/$defs/Missing"}}})


def test_transform_is_idempotent() -> None:
    """Applying it twice changes nothing, so it is safe to compose."""
    once = strict_schema(EditPlan)

    class _Wrapper:
        @staticmethod
        def model_json_schema() -> dict:
            return once

    assert strict_schema(_Wrapper) == once

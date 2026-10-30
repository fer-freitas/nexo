"""Esquemas de saída do planejador (JSON Schema a partir dos modelos da spec)."""

from __future__ import annotations

import copy
from typing import Any

from nexo.kernel.spec import questions as q
from nexo.kernel.spec.model import Spec


def _schema_without(model_schema: dict[str, Any], drop: tuple[str, ...]) -> tuple[dict[str, Any], dict[str, Any]]:
    schema = copy.deepcopy(model_schema)
    defs = schema.pop("$defs", {})
    props = {k: v for k, v in schema["properties"].items() if k not in drop}
    return {"type": "object", "properties": props, "additionalProperties": False}, defs


def planner_schema() -> dict[str, Any]:
    patch, defs = _schema_without(Spec.model_json_schema(), ("id",))
    return {
        "type": "object",
        "$defs": defs,
        "properties": {
            "spec_patch": patch,
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "severity": {"type": "string", "enum": list(q.SEVERITIES)},
                        "section": {"type": "string"},
                    },
                    "required": ["text", "severity"],
                },
            },
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "contract_files": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                    "required": ["path", "content"],
                },
            },
        },
        "required": ["spec_patch", "questions"],
    }

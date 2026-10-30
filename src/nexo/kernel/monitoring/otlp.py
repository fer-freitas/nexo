"""Traces OpenTelemetry: modelo mínimo de span e codificação OTLP/HTTP JSON, sem dependência do SDK.

Atributos passam pela mesma regra da telemetria (só metadados: sem caminhos, prompts ou texto longo). O formato é o
OTLP/HTTP JSON aceito por Azure Monitor (via OpenTelemetry Collector), Datadog, Grafana/Tempo, Jaeger e afins.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

from nexo.kernel.monitoring.telemetry import _clean

STATUS_OK = 1
STATUS_ERROR = 2


def new_id(nbytes: int) -> str:
    return os.urandom(nbytes).hex()


@dataclass
class SpanEvent:
    name: str
    at_ns: int
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class Span:
    name: str
    trace_id: str
    span_id: str
    parent_id: str = ""
    start_ns: int = field(default_factory=time.time_ns)
    end_ns: int = 0
    attrs: dict[str, Any] = field(default_factory=dict)
    events: list[SpanEvent] = field(default_factory=list)
    status: int = STATUS_OK
    status_message: str = ""

    def set(self, **attrs: Any) -> None:
        self.attrs.update(safe_attrs(attrs))

    def event(self, name: str, **attrs: Any) -> None:
        if len(self.events) < 200:  # passo com loop de ferramentas não estoura o span
            self.events.append(SpanEvent(name, time.time_ns(), safe_attrs(attrs)))

    def error(self, message: str) -> None:
        self.status = STATUS_ERROR
        self.status_message = message[:200] if _clean(message[:120]) else "erro"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "start_ns": self.start_ns,
            "end_ns": self.end_ns,
            "attrs": self.attrs,
            "events": [{"name": e.name, "at_ns": e.at_ns, "attrs": e.attrs} for e in self.events],
            "status": self.status,
            "status_message": self.status_message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Span:
        return cls(
            name=str(data["name"]),
            trace_id=str(data["trace_id"]),
            span_id=str(data["span_id"]),
            parent_id=str(data.get("parent_id") or ""),
            start_ns=int(data["start_ns"]),
            end_ns=int(data["end_ns"]),
            attrs=dict(data.get("attrs") or {}),
            events=[
                SpanEvent(str(e["name"]), int(e["at_ns"]), dict(e.get("attrs") or {})) for e in data.get("events") or []
            ],
            status=int(data.get("status", STATUS_OK)),
            status_message=str(data.get("status_message") or ""),
        )


def safe_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    """Só escalares limpos (sem caminhos nem texto longo); o resto é descartado."""
    out: dict[str, Any] = {}
    for key, value in attrs.items():
        if value is None or isinstance(value, (list, dict)):
            continue
        if isinstance(value, str):
            value = value[:120]
        if _clean(value):
            out[str(key)] = value
    return out


def _value(v: Any) -> dict[str, Any]:
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}
    if isinstance(v, float):
        return {"doubleValue": v}
    return {"stringValue": str(v)}


def _attrs(attrs: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"key": k, "value": _value(v)} for k, v in sorted(attrs.items())]


def to_otlp(spans: list[Span], resource: dict[str, Any], scope: str = "nexo") -> dict[str, Any]:
    """Corpo de `POST /v1/traces` (OTLP/HTTP JSON)."""
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": _attrs(safe_attrs(resource))},
                "scopeSpans": [
                    {
                        "scope": {"name": scope},
                        "spans": [
                            {
                                "traceId": s.trace_id,
                                "spanId": s.span_id,
                                **({"parentSpanId": s.parent_id} if s.parent_id else {}),
                                "name": s.name,
                                "kind": 1,
                                "startTimeUnixNano": str(s.start_ns),
                                "endTimeUnixNano": str(s.end_ns or s.start_ns),
                                "attributes": _attrs(s.attrs),
                                "events": [
                                    {"timeUnixNano": str(e.at_ns), "name": e.name, "attributes": _attrs(e.attrs)}
                                    for e in s.events
                                ],
                                "status": {
                                    "code": s.status,
                                    **({"message": s.status_message} if s.status_message else {}),
                                },
                            }
                            for s in spans
                        ],
                    }
                ],
            }
        ]
    }

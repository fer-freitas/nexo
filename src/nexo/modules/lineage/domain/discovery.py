"""Descoberta pela infraestrutura: só diz que alguém consome. Sem repositório conhecido, o nó fica não identificado."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from nexo.modules.lineage.domain.model import SCHEMA, normalize
from nexo.modules.lineage.domain.scan import SCANNER_VERSION


def _link(channel: str) -> dict[str, Any]:
    return {
        "channel": channel,
        "status": "discovered",
        "evidence": {"source": "discovery", "file": "", "line": 0, "confidence": "low"},
    }


def _manifest(team: str, source: str, components: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ordered = [{"id": cid, **components[cid]} for cid in sorted(components)]
    digest = hashlib.sha256(json.dumps(ordered, sort_keys=True).encode("utf-8")).hexdigest()[:40]
    return {
        "schema": SCHEMA,
        "team": team,
        "repo": f"discovery:{source}",
        "commit": digest,
        "generated_at": "",
        "scanner": SCANNER_VERSION,
        "components": ordered,
        "unresolved": [],
    }


def _consume(components: dict[str, dict[str, Any]], cid: str, channel: str) -> None:
    links = components.setdefault(cid, {}).setdefault("consumes", [])
    if not any(link["channel"] == channel for link in links):
        links.append(_link(channel))
        links.sort(key=lambda link: link["channel"])


def _last(arn: str) -> str:
    return arn.rsplit(":", 1)[-1].rsplit("/", 1)[-1]


def from_aws(
    team: str,
    subscriptions: list[dict[str, Any]],
    mappings: list[dict[str, Any]],
    services: list[str],
) -> dict[str, Any]:
    """Assinaturas SNS→SQS, gatilhos de Lambda e serviços ECS. O ID da conta some junto com o ARN."""
    components: dict[str, dict[str, Any]] = {}
    for sub in subscriptions:
        if sub.get("Protocol") == "sqs":
            channel, _ = normalize("sns", str(sub.get("TopicArn", "")))
            _consume(components, f"?sqs:{_last(str(sub.get('Endpoint', '')))}", channel)
    for mapping in mappings:
        source = str(mapping.get("EventSourceArn", ""))
        if source.startswith(("arn:aws:sqs", "arn:aws:sns")):
            channel, _ = normalize("sqs", source)
            _consume(components, f"?lambda:{_last(str(mapping.get('FunctionArn', '')))}", channel)
    for name in services:
        components.setdefault(f"?ecs:{_last(name)}", {"runtime": "ecs"})
    return _manifest(team, "aws", components)


def from_kafka(team: str, groups: dict[str, list[str]]) -> dict[str, Any]:
    """Grupo de consumidor mostra que alguém consome, mesmo sem saber o repositório."""
    components: dict[str, dict[str, Any]] = {}
    for group, topics in groups.items():
        for topic in topics:
            channel, _ = normalize("kafka", str(topic))
            _consume(components, f"?kafka-group:{group}", channel)
    return _manifest(team, "kafka", components)

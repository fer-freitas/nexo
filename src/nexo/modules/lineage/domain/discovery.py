"""Descoberta pela infraestrutura: só diz que alguém consome. Sem repositório conhecido, o nó fica não identificado."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from nexo.modules.lineage.domain.model import SCHEMA, normalize
from nexo.modules.lineage.domain.scan import SCANNER_VERSION


def _link(channel: str, message: str = "") -> dict[str, Any]:
    return {
        "channel": channel,
        **({"message": message} if message else {}),
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


def _contracts(schemas: list[dict[str, Any]]) -> dict[str, str]:
    """Tópico → contrato, pela convenção do schema registry: o assunto `<tópico>-value` descreve a mensagem."""
    found: dict[str, str] = {}
    for schema in schemas:
        subject = str(schema.get("subject", ""))
        if subject.endswith("-value"):
            found[subject.removesuffix("-value")] = f"{subject} v{schema.get('version', '?')}"
    return found


def from_kafka(
    team: str,
    groups: dict[str, list[str]],
    acls: list[dict[str, Any]] | None = None,
    schemas: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Grupo de consumidor e ACL mostram que alguém usa o tópico, mesmo sem saber o repositório.

    ACL com curinga ou prefixo não aponta para um tópico certo e por isso não vira ligação.
    """
    contracts = _contracts(schemas or [])
    components: dict[str, dict[str, Any]] = {}

    def add(cid: str, direction: str, topic: str) -> None:
        channel, _ = normalize("kafka", topic)
        links = components.setdefault(cid, {}).setdefault(direction, [])
        if not any(link["channel"] == channel for link in links):
            links.append(_link(channel, contracts.get(topic, "")))
            links.sort(key=lambda link: link["channel"])

    for group, topics in groups.items():
        for topic in topics:
            add(f"?kafka-group:{group}", "consumes", str(topic))
    for acl in acls or []:
        topic = str(acl.get("topic", ""))
        direction = {"read": "consumes", "write": "publishes"}.get(str(acl.get("operation", "")).lower())
        literal = str(acl.get("pattern", "literal")).lower() == "literal"
        if direction and topic and topic != "*" and literal:
            principal = str(acl.get("principal", "")).removeprefix("User:")
            add(f"?kafka-principal:{principal}", direction, topic)
    return _manifest(team, "kafka", components)

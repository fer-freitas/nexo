"""Descoberta no Kafka: grupos, ACLs e contratos do schema registry, a partir do que o time de Kafka exporta."""

from __future__ import annotations

from nexo.modules.lineage.domain.discovery import from_kafka


def _component(manifest: dict, cid: str) -> dict:  # type: ignore[type-arg]
    return next(c for c in manifest["components"] if c["id"] == cid)


def test_acl_says_who_reads_and_who_writes_and_schema_names_the_contract() -> None:
    manifest = from_kafka(
        "t",
        {"g-boleto": ["pagamentos"]},
        [
            {"principal": "User:svc-cobranca", "topic": "pagamentos", "operation": "Write"},
            {"principal": "User:svc-extrato", "topic": "pagamentos", "operation": "READ"},
        ],
        [{"subject": "pagamentos-value", "version": 4}, {"subject": "pagamentos-key", "version": 9}],
    )
    assert _component(manifest, "?kafka-group:g-boleto")["consumes"][0]["message"] == "pagamentos-value v4"
    writer = _component(manifest, "?kafka-principal:svc-cobranca")["publishes"][0]
    assert writer["status"] == "discovered" and writer["message"] == "pagamentos-value v4"
    assert "consumes" in _component(manifest, "?kafka-principal:svc-extrato")


def test_acl_without_an_exact_topic_does_not_become_a_link() -> None:
    manifest = from_kafka(
        "t",
        {},
        [
            {"principal": "User:a", "topic": "*", "operation": "Read"},
            {"principal": "User:b", "topic": "pag", "operation": "Read", "pattern": "PREFIXED"},
            {"principal": "User:c", "topic": "pag", "operation": "Describe"},
        ],
    )
    assert manifest["components"] == []


def test_only_groups_still_works() -> None:
    assert [c["id"] for c in from_kafka("t", {"g": ["x"]})["components"]] == ["?kafka-group:g"]

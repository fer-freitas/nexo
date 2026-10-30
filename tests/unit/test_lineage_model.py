"""Linhagem: o elo é o canal; o manifesto só leva metadados; nunca se afirma que não há consumidor."""

from __future__ import annotations

import pytest

from nexo.modules.lineage.adapters import scheduler
from nexo.modules.lineage.domain import model as m
from nexo.modules.lineage.domain.scan import build_manifest, normalize


@pytest.mark.parametrize(
    "kind,raw,channel,relation",
    [
        ("kafka", "hom-posicao.traduzida", "kafka:posicao.traduzida", ""),
        ("kafka", "prd.posicao.traduzida", "kafka:posicao.traduzida", ""),
        ("kafka", "posicao.traduzida.retry.2", "kafka:posicao.traduzida", "retry"),
        ("kafka", "posicao.traduzida.dlt", "kafka:posicao.traduzida", "dlt"),
        ("sns", "arn:aws:sns:us-east-1:123456789012:posicao-atualizada", "sns:posicao-atualizada", ""),
        ("sqs", "arn:aws:sqs:us-east-1:123456789012:posicao-correcoes-prod", "sqs:posicao-correcoes", ""),
    ],
)
def test_normalize_removes_environment_account_and_groups_retry(kind: str, raw: str, channel: str, relation: str) -> None:
    assert normalize(kind, raw) == (channel, relation)


TRADUTOR = {
    "Tradutor.Worker/Tradutor.Worker.csproj": '<Project Sdk="Microsoft.NET.Sdk.Worker"></Project>',
    "Tradutor.Worker/Program.cs": "var b = Host.CreateApplicationBuilder(args);",
    "Tradutor.Worker/Publisher.cs": 'class P { async Task Go() { await _producer.ProduceAsync("hom-posicao.traduzida", msg); } }',
    "Tradutor.Worker/appsettings.json": '{"Kafka": {"Password": "s3nh4-nao-pode"}}',
    "Tradutor.Tests/Tradutor.Tests.csproj": "<Project></Project>",
    "Tradutor.Tests/FakeProducer.cs": 'p.ProduceAsync("topico-de-teste", m);',
}
GESTAO = {
    "src/Gestao.Api/Gestao.Api.csproj": '<Project Sdk="Microsoft.NET.Sdk.Web"></Project>',
    "src/Gestao.Api/Program.cs": 'var b = WebApplication.CreateBuilder(args); app.MapGet("/posicoes", () => 1);',
    "src/Gestao.Api/Consumer.cs": 'consumer.Subscribe("prd-posicao.traduzida");\nvar t = TopicArn = Topics.Atualizada;\nclass Topics { const string Atualizada = "arn:aws:sns:us-east-1:123456789012:posicao-atualizada"; }',
    "src/Gestao.Api/Dynamic.cs": 'consumer.Subscribe($"{prefixo}.eventos");',
    "src/Gestao.Api/Fila.cs": 'await sqs.ReceiveMessageAsync(_config["Filas:Correcoes"]);',
    "src/Gestao.Api/appsettings.json": '{"Filas": {"Correcoes": "posicao-correcoes"}}',
}


def manifest(team: str, repo: str, tree: dict[str, str]) -> dict:
    return build_manifest(team, f"github:org/{repo}", "abc", "", tree, repo)


def test_scan_finds_direction_channel_and_evidence_without_secrets() -> None:
    data = manifest("time-traducao", "tradutor", TRADUTOR)
    comp = data["components"][0]
    assert comp["id"] == "time-traducao/tradutor-worker"
    link = comp["publishes"][0]
    assert link["channel"] == "kafka:posicao.traduzida"
    assert link["evidence"]["file"] == "Tradutor.Worker/Publisher.cs" and link["evidence"]["line"] == 1
    text = str(data)
    assert "s3nh4" not in text and "topico-de-teste" not in text
    assert m.validate_manifest(data, "time-traducao") == []


def test_scan_resolves_constant_and_config_and_marks_dynamic_as_unresolved() -> None:
    data = manifest("time-posicao", "gestao", GESTAO)
    comp = next(c for c in data["components"] if c["id"].endswith("gestao-api"))
    consumes = {x["channel"] for x in comp["consumes"]}
    assert consumes == {"kafka:posicao.traduzida", "sqs:posicao-correcoes"}
    assert {x["channel"] for x in comp["publishes"]} == {"sns:posicao-atualizada"}
    assert any(u["file"].endswith("Dynamic.cs") for u in data["unresolved"])
    assert "123456789012" not in str(data)
    assert comp["exposes"][0]["channel"] == "http:gestao-api"


def test_manifest_is_deterministic() -> None:
    assert manifest("t", "g", GESTAO) == manifest("t", "g", dict(reversed(list(GESTAO.items()))))


TF = """
resource "aws_sns_topic" "atual" { name = "posicao-atualizada" }
resource "aws_sqs_queue" "risco" { name = "risco-entrada" }
resource "aws_lambda_function" "calc" { function_name = "calculo-risco" }
resource "aws_sns_topic_subscription" "s" {
  topic_arn = aws_sns_topic.atual.arn
  protocol  = "sqs"
  endpoint  = aws_sqs_queue.risco.arn
}
resource "aws_lambda_event_source_mapping" "m" {
  event_source_arn = aws_sqs_queue.risco.arn
  function_name    = aws_lambda_function.calc.arn
}
"""


def test_terraform_links_sns_to_sqs_to_lambda() -> None:
    data = manifest("time-risco", "risco", {"infra/main.tf": TF})
    comp = next(c for c in data["components"] if c["id"] == "time-risco/calculo-risco")
    assert comp["runtime"] == "lambda"
    assert {x["channel"] for x in comp["consumes"]} == {"sqs:risco-entrada", "sns:posicao-atualizada"}
    assert comp["consumes"][0]["evidence"]["source"] == "terraform"


def test_asyncapi_contract_gives_message_and_high_confidence() -> None:
    contract = """
asyncapi: 2.6.0
info: {title: t, version: 3.0.0}
channels:
  posicao.traduzida:
    bindings: {kafka: {}}
    subscribe:
      message: {name: PosicaoTraduzida}
"""
    data = manifest("t", "tradutor", {"contracts/posicao.asyncapi.yaml": contract})
    link = data["components"][0]["publishes"][0]
    assert link["message"] == "PosicaoTraduzida@3" and link["evidence"]["confidence"] == "high"


def graph(*manifests: dict) -> m.Graph:
    g = m.Graph()
    for data in manifests:
        m.add_manifest(g, data)
    return g


def test_repositories_are_linked_by_the_channel() -> None:
    g = graph(manifest("time-traducao", "tradutor", TRADUTOR), manifest("time-posicao", "gestao", GESTAO))
    data = m.neighbors(g, "time-posicao/gestao-api")
    entry = next(r for r in data["entries"] if r["channel"] == "kafka:posicao.traduzida")
    assert entry["component"] == "time-traducao/tradutor-worker" and entry["state"] == m.CONFIRMED
    other = next(r for r in data["entries"] if r["channel"] == "sqs:posicao-correcoes")
    assert other["component"] == "?" and other["state"] == m.PARTIAL
    assert m.walk(g, "time-posicao/gestao-api", "upstream") == ["time-traducao/tradutor-worker"]


def test_impact_says_known_consumers_never_no_consumers() -> None:
    g = graph(manifest("time-traducao", "tradutor", TRADUTOR))
    out = m.impact(g, "kafka:posicao.traduzida")
    assert out["known_consumers"] == [] and not out["complete"] and "conhecidos" in out["note"]


def test_coverage_and_stale() -> None:
    g = graph(manifest("time-traducao", "tradutor", TRADUTOR), manifest("time-posicao", "gestao", GESTAO))
    out = m.coverage(g)
    assert out["confirmed"] == 1 and out["channels"] >= 3 and out["unresolved"] == 1
    g.stale_teams.add("time-traducao")
    assert m.coverage(g)["by_state"][m.STALE] == 1


@pytest.mark.parametrize(
    "change,message",
    [
        ({"code": "x"}, "campos proibidos"),
        ({"team": "outro"}, "difere"),
        ({"repo": "conta 123456789012"}, "conta da AWS"),
        ({"schema": "x"}, "schema"),
    ],
)
def test_reader_refuses_invalid_manifest(change: dict, message: str) -> None:
    data = {**manifest("t", "tradutor", TRADUTOR), **change}
    assert any(message in p for p in m.validate_manifest(data, "t"))


@pytest.mark.parametrize("system,needle", [("Windows", "schtasks"), ("Darwin", "launchctl"), ("Linux", "systemctl")])
def test_scheduler_plan_per_system_no_admin(system: str, needle: str, tmp_path) -> None:  # type: ignore[no-untyped-def]
    p = scheduler.plan(system, "C:/nx/nexo.exe", tmp_path)
    assert p.install[0][0] == needle or p.install[-1][0] == needle
    joined = " ".join(" ".join(c) for c in p.install)
    assert "sudo" not in joined and "/RU" not in joined


def test_hook_preserves_existing_and_is_removed(tmp_path) -> None:  # type: ignore[no-untyped-def]
    hooks = tmp_path / ".git" / "hooks"
    hooks.mkdir(parents=True)
    path = hooks / "post-merge"
    path.write_text("#!/bin/sh\necho meu hook\n", encoding="utf-8")
    scheduler.install_hook(tmp_path, "nexo")
    scheduler.install_hook(tmp_path, "nexo")
    text = path.read_text(encoding="utf-8")
    assert "echo meu hook" in text and text.count(scheduler.MARK) == 1 and "&" in text
    scheduler.remove_hook(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "echo meu hook" in text and scheduler.MARK not in text


def test_commit_hash_with_many_digits_is_not_an_account_id() -> None:
    data = {**manifest("t", "tradutor", TRADUTOR), "commit": "a123456789012b"}
    assert m.validate_manifest(data, "t") == []

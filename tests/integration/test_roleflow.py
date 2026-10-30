"""fluxo por papéis e discovery: sessão conjunta, pacote único e aprovação de cada papel, sem passagem manual."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from nexo.kernel import roles as r
from nexo.kernel.backlog import Backlog
from nexo.kernel.spec.model import TEST_TYPES, Risk, Spec
from tests.conftest import Runner, dump_yaml
from tests.integration.test_plan import BACKLOG

DISCOVERY = {
    "done": {
        "structured": {
            "title": "Endpoint de versão",
            "problem": "SRE não sabe qual versão está em produção durante incidentes",
            "audience": ["SRE"],
            "opportunities": ["reduzir o tempo de diagnóstico"],
            "evidence": [{"source": "postmortems 2026", "finding": "4 incidentes com versão errada"}],
            "hypotheses": [
                {"id": "H-1", "statement": "expor a versão reduz o MTTR", "metric": "MTTR", "target": "-20%"}
            ],
            "experiments": [{"hypothesis": "H-1", "method": "piloto em 1 serviço"}],
            "open_questions": [],
            "recommendation": "build",
        }
    }
}
CONTRACT = {
    "path": "contracts/version.yaml",
    "content": "openapi: 3.1.0\ninfo: {title: v, version: '1'}\npaths:\n  /version:\n    get:\n"
    "      responses:\n        '200': {description: ok}\n",
}
READY = {
    "done": {
        "structured": {
            "spec_patch": {
                "title": "Endpoint de versão",
                "objective": "Consultar versão implantada",
                "problem": "Não sabemos a versão em produção",
                "personas": ["SRE"],
                "scope_in": ["função version"],
                "scope_out": ["UI"],
                "acceptance": [
                    {
                        "id": "AC-1",
                        "title": "Retorna versão",
                        "given": ["serviço"],
                        "when": ["chamo"],
                        "then": ["0.1.0"],
                    }
                ],
                "data_class": "internal",
                "contracts": [{"id": "C-1", "type": "openapi", "path": "contracts/version.yaml"}],
                "nfr": {"performance": "< 1 ms", "security": "nada sensível", "availability": "99,9%"},
                "dependencies_none": True,
                "test_strategy": {t: "sim" for t in TEST_TYPES},
                "risks": [{"area": "auth", "description": "rota pública sem autenticação"}],
            },
            "questions": [],
            "contract_files": [CONTRACT],
        }
    }
}
DESIGN_FIX = {  # retrabalho do tech lead: só muda o design (NFR), requisitos intactos
    "done": {
        "structured": {
            "spec_patch": {
                "nfr": {"performance": "< 5 ms p99", "security": "rota atrás do gateway", "availability": "99,9%"}
            },
            "questions": [],
        }
    }
}
ROLES = {"pm": ["os:ana"], "tech_lead": ["os:bruno"], "dev": ["os:caio", "os:ana"]}


def enable_roleflow(nexo_home: Path, **policy: Any) -> None:
    dump_yaml(
        nexo_home / "brain" / "org" / "config.yaml",
        {"planning": {"role_flow": {"enabled": True, **policy}}, "roles": ROLES},
    )


def as_(monkeypatch: pytest.MonkeyPatch, who: str) -> None:
    monkeypatch.setenv("NX_ACTOR", who)


def call(nexo: Runner, *args: str) -> tuple[int, dict[str, Any], str]:
    code, out, err = nexo("--json", *args)
    return code, (json.loads(out) if out.strip() else {}), err


def parts(view: dict[str, Any]) -> dict[str, str]:
    return {p["part"]: p["status"] for p in view["parts"]}


def test_role_flow_end_to_end(  # type: ignore[no-untyped-def]
    project: Path, nexo: Runner, scenario, nexo_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable_roleflow(nexo_home)
    # papéis vindos do projeto são ignorados: só org/squad definem quem é PM
    dump_yaml(project / ".nexo" / "nexo.yaml", {"project": "svc", "roles": {"pm": ["os:bruno"]}})
    scenario([{"tool": "claude", "events": [e]} for e in (DISCOVERY, READY, BACKLOG, BACKLOG, DESIGN_FIX, BACKLOG)])

    # 1. discovery do PM
    as_(monkeypatch, "ana")
    code, disc, err = call(nexo, "plan", "discovery", "4 incidentes por versão errada em 2026")
    assert code == 0, err
    assert disc["feature"] == "F-1" and disc["recommendation"] == "build" and disc["missing"] == []
    folder = Path(disc["folder"])
    assert (folder / "discovery.md").is_file() and "H-1" in (folder / "discovery.md").read_text(encoding="utf-8")
    assert call(nexo, "plan", "inbox")[1]["items"][0]["action"] == "decidir discovery"

    # 2. só o PM decide; build leva o discovery direto ao refinamento (sem reescrever nada)
    as_(monkeypatch, "bruno")
    code, _, err = call(nexo, "plan", "decide", "F-1", "build", "--reason", "evidência suficiente")
    assert code != 0 and "PM" in err
    as_(monkeypatch, "ana")
    code, out, err = call(nexo, "plan", "decide", "F-1", "build", "--reason", "evidência suficiente nos postmortems")
    assert code == 0, err
    assert out["state"] == "Refinamento" and out["plan"]["ready"]
    assert any(
        i.startswith("discovery-brief") for i in (p.name for p in (Path(out["plan"]["folder"]) / "inputs").iterdir())
    )
    folder = Path(out["plan"]["folder"])

    # 3. aprovação única desligada no fluxo por papéis
    code, _, err = call(nexo, "plan", "approve", "F-1")
    assert code != 0 and "--part" in err

    # 4. pacote gerado; risco em auth exige sessão conjunta antes das aprovações
    code, view, err = call(nexo, "plan", "package", "F-1")
    assert code == 0, err
    assert view["packaged"] and view["joint_pending"] and "auth" in view["joint_reasons"][0]
    code, _, err = call(nexo, "plan", "approve", "F-1", "--part", "requisitos")
    assert code != 0 and "sessão conjunta" in err

    # 5. sessão conjunta: participante precisa ter o papel; quem abre participa
    as_(monkeypatch, "bruno")
    code, _, err = call(
        nexo, "plan", "session", "start", "F-1", "--pm", "os:ana", "--lead", "os:bruno", "--dev", "os:zeca"
    )
    assert code != 0 and "os:zeca" in err
    code, s, err = call(
        nexo, "plan", "session", "start", "F-1", "--pm", "os:ana", "--lead", "os:bruno", "--dev", "os:caio"
    )
    assert code == 0 and s["missing_roles"] == [], err
    as_(monkeypatch, "caio")
    code, view, err = call(nexo, "plan", "session", "close", "F-1", "--notes", "combinado: rota atrás do gateway")
    assert code == 0, err
    assert not view["joint_pending"] and parts(view) == dict.fromkeys(r.PARTS, "pendente")

    # 6. cada papel aprova a sua parte; testes e plano são aprovados depois, por história
    assert call(nexo, "plan", "inbox")[1]["items"] == []
    code, _, err = call(nexo, "plan", "approve", "F-1", "--part", "design")
    assert code != 0 and "tech lead" in err
    as_(monkeypatch, "ana")
    code, view, _ = call(nexo, "plan", "approve", "F-1", "--part", "requisitos")
    assert parts(view) == {"requisitos": "aprovada", "design": "pendente"}

    # 7. tech lead rejeita o design: a IA refaz com o motivo; requisitos seguem aprovados
    as_(monkeypatch, "bruno")
    code, view, err = call(nexo, "plan", "reject", "F-1", "--part", "design", "--reason", "p99 e rota atrás do gateway")
    assert code == 0, err
    assert parts(view) == {"requisitos": "aprovada", "design": "pendente"}
    assert "gateway" in (folder / "spec.yaml").read_text(encoding="utf-8")

    # 8. última aprovação: backlog, tarefas e documentos gerados; estado Pronto
    code, view, err = call(nexo, "plan", "approve", "F-1", "--part", "design")
    assert code == 0, err
    assert view["state"] == "Pronto" and parts(view) == dict.fromkeys(r.PARTS, "aprovada")
    assert (folder / "backlog.yaml").is_file() and (project / ".nexo" / "tasks" / "T-1-2.yaml").is_file()
    logs = json.loads(nexo("--json", "logs", "-n", "400")[1])
    gates = [
        e["payload"]
        for e in logs
        if e["type"] == "gate.decision" and str(e["payload"].get("gate", "")).startswith("role:")
    ]
    assert {(g["gate"], g["decision"]) for g in gates} >= {("role:design", "rejected"), ("role:design", "approved")}
    sent = [e["payload"]["title"] for e in logs if e["type"] == "notify" and e["payload"]["event"] == "planning"]
    assert any("pronta para desenvolvimento" in t for t in sent) and any("Pacote" in t for t in sent)
    final = next(
        e["payload"]["message"] for e in logs if e["type"] == "notify" and "pronta para" in e["payload"]["title"]
    )
    assert "spec.md" in final and "nexo run feature F-1" in final


def test_small_feature_skips_joint_session_and_drop(  # type: ignore[no-untyped-def]
    project: Path, nexo: Runner, scenario, nexo_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable_roleflow(nexo_home, allow_same_person=True)
    low_risk = json.loads(json.dumps(READY))
    patch = low_risk["done"]["structured"]["spec_patch"]
    patch.pop("risks")
    patch["risks_reviewed"] = True
    scenario([{"tool": "claude", "events": [e]} for e in (low_risk, BACKLOG, DISCOVERY)])
    as_(monkeypatch, "ana")
    assert call(nexo, "plan", "versão do serviço")[0] == 0  # refinamento direto (sem discovery) segue valendo
    code, view, err = call(nexo, "plan", "package", "F-1")
    assert code == 0 and not view["joint_pending"] and view["joint_reasons"] == [], err
    assert call(nexo, "plan", "approve", "F-1", "--part", "requisitos")[0] == 0
    # discovery descartado vira Cancelado
    code, disc, _ = call(nexo, "plan", "discovery", "ideia sem dono")
    code, out, err = call(nexo, "plan", "decide", disc["feature"], "drop", "--reason", "não compensa agora")
    assert code == 0 and out["state"] == "Cancelado", err


def test_role_flow_needs_policy_and_flags(  # type: ignore[no-untyped-def]
    project: Path, nexo: Runner, nexo_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, _, err = call(nexo, "plan", "session", "start", "F-1", "--pm", "os:ana")
    assert code != 0 and "planning.role_flow.enabled" in err
    enable_roleflow(nexo_home)
    dump_yaml(nexo_home / "brain" / "org" / "flags.yaml", {"flags": {"discovery": False}})
    code, _, err = call(nexo, "plan", "discovery", "qualquer coisa")
    assert code != 0 and "feature flag" in err


# ------------------------------------------------------------------ domínio


def _spec(**kw: Any) -> Spec:
    return Spec(id="F-1", title="t", objective="o", **kw)


def test_digests_chain_and_joint_policy() -> None:
    draft = Backlog.model_validate({"feature": "F-1", "stories": [{"id": "US-1-1", "title": "a", "points": 8}]})
    base = r.part_digests(_spec(), draft)
    changed_req = r.part_digests(_spec(problem="novo"), draft)
    assert all(changed_req[p] != base[p] for p in r.PARTS)  # requisito muda → tudo a reaprovar
    changed_design = r.part_digests(_spec(nfr={"performance": "x"}), draft)
    assert changed_design["requisitos"] == base["requisitos"] and changed_design["design"] != base["design"]
    assert r.part_digests(_spec(), None)["plano"] != base["plano"]
    auto = r.JointPolicy()
    assert r.joint_reasons(_spec(), draft, auto) == []
    assert r.joint_reasons(_spec(risks=[Risk(area="payments", description="d")]), draft, auto)
    assert r.joint_reasons(_spec(data_class="restricted"), None, auto) == ["dados restritos"]
    big = Backlog.model_validate(
        {"feature": "F-1", "stories": [{"id": f"US-1-{i}", "title": "a", "points": 2} for i in range(7)]}
    )
    assert any("histórias" in x for x in r.joint_reasons(_spec(), big, auto))
    assert r.joint_reasons(_spec(), None, r.JointPolicy(mode="always"))
    assert r.joint_reasons(_spec(data_class="restricted"), None, r.JointPolicy(mode="never")) == []


def test_discovery_gaps_and_status() -> None:
    d = r.Discovery(problem="problema real", opportunities=["o"])
    assert r.discovery_missing(d) == ["evidência", "hipótese com métrica de sucesso"]
    with pytest.raises(ValueError):
        r.Hypothesis(id="X-1", statement="s")
    rec = r.RoleRecord(
        approvals=[r.Approval(part="requisitos", by="a", at="1", digest="d1")],
        rejections=[r.Rejection(part="design", by="b", at="2", reason="motivo", digest="d2")],
        sessions=[
            r.Session(id="S-1", started_at="0", started_by="a", participants={"pm": ["a"], "dev": ["c"]}, closed_at="1")
        ],
    )
    st = {s.part: s.status for s in r.part_status(rec, {"requisitos": "d1", "design": "d2", "plano": "d3"})}
    assert st == {"requisitos": "aprovada", "design": "rejeitada"}
    st = {s.part: s.status for s in r.part_status(rec, {"requisitos": "zz", "design": "novo", "plano": "d3"})}
    assert st["requisitos"] == "desatualizada" and st["design"] == "pendente"
    assert not rec.joint_done()  # faltou o tech lead

"""telemetria central automática — outbox, envio, deduplicação, identidade de dispositivo, contrato, atraso,
pseudonimização, conciliação e retenção. Coletor testado com SQLite e PostgreSQL (pgserver)."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from nexo.kernel.monitoring.telemetry import CONTRACT, validate
from nexo.kernel.runtime import http
from nexo.kernel.secrets import credentials
from tests.conftest import DEFAULT_TASK, Runner, dump_yaml, edit

URL = "https://coletor.empresa.com"
TOKENS = {
    "tok-admin": {"role": "admin", "dev": "ana.admin"},
    "tok-sso-bia": {"role": "dev", "dev": "bia@empresa.com"},  # sessão SSO da Bia (página de verificação)
    "tok-sso-caio": {"role": "dev", "dev": "caio@empresa.com"},
    "tok-gestor": {"role": "manager", "dev": "gestor@empresa.com"},
    "tok-finops": {"role": "finops", "dev": "finops@empresa.com"},
}
DONE = {"progress": {"percent": 100}}
USAGE = {"usage": {"api_equiv_usd": 1.0, "tokens_in": 1200, "tokens_out": 300}}


@pytest.fixture(scope="session")
def pg_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Any]:
    pgserver = pytest.importorskip("pgserver")
    srv = pgserver.get_server(str(tmp_path_factory.mktemp("pg")), cleanup_mode="stop")
    yield srv
    srv.cleanup()


@pytest.fixture(params=["sqlite", "postgres"])
def central(request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from fastapi.testclient import TestClient

    if request.param == "postgres":
        import psycopg

        srv = request.getfixturevalue("pg_server")
        name = f"nexo_{uuid.uuid4().hex[:10]}"
        with psycopg.connect(srv.get_uri(), autocommit=True) as conn:
            conn.execute(f"CREATE DATABASE {name}")
        from urllib.parse import urlparse

        url = urlparse(srv.get_uri())._replace(path=f"/{name}").geturl()
        monkeypatch.setenv("NX_COLLECTOR_DB", url)
    else:
        monkeypatch.setenv("NX_COLLECTOR_DB", str(tmp_path / "coletor.db"))
    monkeypatch.setenv("NX_COLLECTOR_TOKENS", json.dumps(TOKENS))
    monkeypatch.setenv("NX_COLLECTOR_PSEUDO_KEY", "chave-de-teste")
    from nexo.central import app as collector_app

    collector_app._dbs.clear()
    client = TestClient(collector_app.app)
    state = {"down": False}

    def transport(method: str, url: str, headers: dict[str, str], data: bytes | None) -> http.Response:
        if state["down"]:
            raise OSError("coletor fora do ar")
        resp = client.request(method, url.replace(URL, ""), headers=headers, content=data)
        return http.Response(resp.status_code, http._decode(resp.content))  # mesmo decode do transporte real

    http.set_transport(transport)
    client.state = state  # type: ignore[attr-defined]
    client.backend = request.param  # type: ignore[attr-defined]
    yield client
    http.set_transport(None)
    for db in collector_app._dbs.values():
        db.close()
    collector_app._dbs.clear()


def enable(nexo_home: Path, **extra: Any) -> None:
    org = nexo_home / "brain" / "org"
    org.mkdir(parents=True, exist_ok=True)
    cfg = {"telemetry": {"enabled": True, "url": URL, "background": False, **extra}}
    existing = (org / "config.yaml").read_text(encoding="utf-8") if (org / "config.yaml").is_file() else ""
    dump_yaml(org / "config.yaml", cfg)
    (org / "config.yaml").write_text(existing + (org / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8")


def login(nexo_home: Path, central: Any, sso: str) -> str:
    from nexo.kernel.context import AppContext
    from nexo.kernel.platform import telemetry

    ctx = AppContext(cwd=nexo_home, home=nexo_home)

    def approve(uri: str, code: str) -> None:
        resp = central.post("/v2/device/approve", json={"user_code": code}, headers={"Authorization": f"Bearer {sso}"})
        assert resp.status_code == 200, resp.text

    data = telemetry.login_central(ctx, approve, wait=0)
    ctx.close()
    return str(data["device_id"])


def outbox(nexo_home: Path) -> list[sqlite3.Row]:
    db = sqlite3.connect(nexo_home / "nexo.db")
    db.row_factory = sqlite3.Row
    return list(db.execute("SELECT * FROM outbox ORDER BY id"))


def run_task(project: Path, nexo: Runner, scenario, tid: str = "T-1") -> None:  # type: ignore[no-untyped-def]
    if not (project / ".nexo" / "tasks" / f"{tid}.yaml").is_file():
        dump_yaml(project / ".nexo" / "tasks" / f"{tid}.yaml", DEFAULT_TASK)
    scenario([{"tool": "claude", "task": tid, "events": [edit(), DONE, USAGE]}])
    assert json.loads(nexo("--json", "run", "task", tid)[1])["state"] == "Concluido"


def test_outbox_is_transactional_projected_and_visible(project: Path, nexo: Runner, scenario, nexo_home: Path) -> None:  # type: ignore[no-untyped-def]
    run_task(project, nexo, scenario)
    assert outbox(nexo_home) == []  # desligada: nada sai nem se acumula
    enable(nexo_home)
    run_task(project, nexo, scenario, "T-2")
    rows = outbox(nexo_home)
    kinds = {r["kind"] for r in rows}
    assert kinds == {"event", "step"} and all(r["status"] == "pending" for r in rows)
    for r in rows:
        assert validate(r["kind"], r["type"], json.loads(r["payload"])) is None  # cumpre o contrato
    step = next(json.loads(r["payload"]) for r in rows if r["kind"] == "step" and "developer" in r["payload"])
    assert step["usage_brl"] == 5.4 and step["tokens_in"] == 1200 and step["tool"] == "claude"
    dump = json.dumps([json.loads(r["payload"]) for r in rows])
    assert str(project) not in dump and "src/" not in dump  # nenhum caminho sai
    shown = json.loads(nexo("--json", "telemetry", "show", "--pending", "--limit", "500")[1])
    assert len(shown) == len(rows) and shown[0]["status"] == "pending"


def test_device_login_flush_dedup_and_pseudonym(
    project: Path, nexo: Runner, scenario, nexo_home: Path, central: Any
) -> None:  # type: ignore[no-untyped-def]
    enable(nexo_home)
    device = login(nexo_home, central, "tok-sso-bia")
    assert "access_token" in json.loads(credentials.get_secret(f"central:{device}") or "{}")
    run_task(project, nexo, scenario)  # envio automático ao fim do passo (background: false nos testes)
    assert all(r["status"] == "sent" for r in outbox(nexo_home))
    measured = central.get(
        "/v2/finops/measured", params={"month": time.strftime("%Y-%m")}, headers={"Authorization": "Bearer tok-finops"}
    ).json()
    assert measured["usage_brl"] == {"claude": 5.4}
    # reenvio (ex.: resposta perdida) não duplica
    db = sqlite3.connect(nexo_home / "nexo.db")
    db.execute("UPDATE outbox SET status = 'pending'")
    db.commit()
    again = json.loads(nexo("--json", "telemetry", "flush")[1])
    assert again["accepted"] == 0 and again["duplicate"] == again["sent"] > 0
    from nexo.central.app import db as collector_db
    from nexo.central.app import pseudonym

    rows = collector_db().query("SELECT DISTINCT dev_pid, device_id FROM raw_events")
    assert rows == [{"dev_pid": pseudonym("bia@empresa.com"), "device_id": device}]
    assert "bia" not in json.dumps(collector_db().query("SELECT payload FROM raw_events"))


def test_offline_backoff_then_recovery(project: Path, nexo: Runner, scenario, nexo_home: Path, central: Any) -> None:  # type: ignore[no-untyped-def]
    enable(nexo_home)
    login(nexo_home, central, "tok-sso-bia")
    central.state["down"] = True
    run_task(project, nexo, scenario)  # o dev trabalha normalmente offline
    rows = outbox(nexo_home)
    assert rows and all(
        r["status"] == "pending" and r["attempts"] == 1 and r["next_attempt_at"] > time.time() for r in rows
    )
    assert json.loads(nexo("--json", "telemetry", "flush")[1])["sent"] == 0  # respeita a espera crescente
    central.state["down"] = False
    db = sqlite3.connect(nexo_home / "nexo.db")
    db.execute("UPDATE outbox SET next_attempt_at = 0")
    db.commit()
    result = json.loads(nexo("--json", "telemetry", "flush")[1])
    assert result["error"] is None and result["pending"] == 0 and result["accepted"] == len(rows)


def test_two_devs_one_offline_central_has_everything_once(
    workdir: Path,
    nexo: Runner,
    scenario,
    nexo_home: Path,
    central: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    import shutil

    from tests.conftest import EXAMPLES, git_cmd

    homes = {"bia": nexo_home, "caio": tmp_path / "home-caio"}
    totals = {}
    for dev, home in homes.items():
        monkeypatch.setenv("NX_HOME", str(home))
        root = workdir / f"svc-{dev}"
        shutil.copytree(EXAMPLES / "python-svc", root)
        git_cmd(root, "init", "-q")
        monkeypatch.chdir(root)
        nexo("init")
        git_cmd(root, "add", "-A")
        git_cmd(root, "commit", "-qm", "init")
        enable(home)
        login(home, central, f"tok-sso-{dev}")
        central.state["down"] = dev == "caio"  # Caio trabalha offline
        run_task(root, nexo, scenario)
        run_task(root, nexo, scenario, "T-2")
        totals[dev] = len(outbox(home))
    central.state["down"] = False
    db = sqlite3.connect(homes["caio"] / "nexo.db")
    db.execute("UPDATE outbox SET next_attempt_at = 0")
    db.commit()
    assert json.loads(nexo("--json", "telemetry", "flush")[1])["pending"] == 0
    from nexo.central.app import db as collector_db

    per_dev = collector_db().query("SELECT dev_pid, COUNT(*) AS n FROM raw_events GROUP BY dev_pid ORDER BY dev_pid")
    assert sorted(int(r["n"]) for r in per_dev) == sorted(totals.values()) and len(per_dev) == 2
    facts = collector_db().query("SELECT COUNT(*) AS n, SUM(usage_brl) AS brl FROM fact_step WHERE role = 'developer'")
    assert int(facts[0]["n"]) == 4 and round(float(facts[0]["brl"]), 2) == 21.6


def test_contract_rejects_extra_fields_paths_and_wrong_version(central: Any) -> None:
    headers = {"Authorization": "Bearer tok-sso-bia"}
    base = {"contract": CONTRACT, "device_id": "d1", "project": "p"}
    events = [
        {"event_id": "ok", "kind": "event", "type": "run.start", "at": "2026-09-25", "payload": {"task": "T-1"}},
        {"event_id": "extra", "kind": "event", "type": "run.start", "at": "x", "payload": {"prompt": "segredo"}},
        {"event_id": "path", "kind": "event", "type": "run.start", "at": "x", "payload": {"task": "/home/ana/.ssh/id"}},
        {"event_id": "tipo", "kind": "event", "type": "arquivo.lido", "at": "x", "payload": {}},
    ]
    body = central.post("/v2/events", json={**base, "events": events}, headers=headers).json()
    assert body["accepted"] == ["ok"] and {r["event_id"] for r in body["rejected"]} == {"extra", "path", "tipo"}
    assert (
        central.post(
            "/v2/events", json={**base, "contract": "nexo.telemetry/v0", "events": []}, headers=headers
        ).status_code
        == 400
    )
    assert central.post("/v2/events", json={**base, "events": []}).status_code == 401


def test_revocation_and_token_refresh(project: Path, nexo: Runner, scenario, nexo_home: Path, central: Any) -> None:  # type: ignore[no-untyped-def]
    enable(nexo_home)
    device = login(nexo_home, central, "tok-sso-bia")
    tokens = json.loads(credentials.get_secret(f"central:{device}") or "{}")
    credentials.set_secret(
        f"central:{device}", json.dumps({**tokens, "expires_at": time.time() + 10})
    )  # quase expirado
    run_task(project, nexo, scenario)
    renewed = json.loads(credentials.get_secret(f"central:{device}") or "{}")
    assert renewed["access_token"] != tokens["access_token"] and all(r["status"] == "sent" for r in outbox(nexo_home))
    assert (
        central.post(f"/v2/devices/{device}/revoke", headers={"Authorization": "Bearer tok-sso-bia"}).status_code == 403
    )
    assert (
        central.post(f"/v2/devices/{device}/revoke", headers={"Authorization": "Bearer tok-admin"}).status_code == 200
    )
    run_task(project, nexo, scenario, "T-2")
    status = json.loads(nexo("--json", "telemetry", "status")[1])
    assert status["pending"] > 0 and "revogado" in (status["last_error"] or "")


def test_delay_warns_then_blocks_agents(project: Path, nexo: Runner, scenario, nexo_home: Path) -> None:  # type: ignore[no-untyped-def]
    enable(nexo_home)
    run_task(project, nexo, scenario)  # sem login: tudo fica pendente
    db = sqlite3.connect(nexo_home / "nexo.db")
    db.execute("UPDATE outbox SET created_at = ?", (time.time() - 4 * 86400,))
    db.commit()
    code, _, err = nexo("status")
    assert code == 0 and "telemetria sem enviar há 4.0 dias — sincronize logo" in err
    db.execute("UPDATE outbox SET created_at = ?", (time.time() - 8 * 86400,))
    db.commit()
    dump_yaml(project / ".nexo" / "tasks" / "T-2.yaml", DEFAULT_TASK)
    code, _, err = nexo("run", "task", "T-2")
    assert code != 0 and "execuções de agente bloqueadas" in err
    checks = {c["name"]: c for c in json.loads(nexo("--json", "doctor")[1])["checks"]}
    assert checks["telemetry"]["status"] == "fail"


def test_dev_cannot_disable_or_redirect_telemetry(project: Path, nexo: Runner, scenario, nexo_home: Path) -> None:  # type: ignore[no-untyped-def]
    enable(nexo_home)
    nexo("config", "set", "telemetry.enabled", "false")
    nexo("config", "set", "telemetry.url", "https://meu-servidor.example", "--project")
    status = json.loads(nexo("--json", "telemetry", "status")[1])
    assert status["enabled"] is True and status["ignored_settings"] == ["telemetry.enabled", "telemetry.url"]
    run_task(project, nexo, scenario)
    assert outbox(nexo_home)  # continua gravando


def test_budget_reconcile_purge_and_reidentify(
    project: Path, nexo: Runner, scenario, nexo_home: Path, central: Any, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    enable(nexo_home)
    org = nexo_home / "brain" / "org" / "config.yaml"
    org.write_text(
        org.read_text(encoding="utf-8") + "budget:\n  source: central\n  external:\n    ttl_seconds: 0\n",
        encoding="utf-8",
    )
    device = login(nexo_home, central, "tok-sso-bia")
    month = time.strftime("%Y-%m")
    put = central.put(
        "/v1/budgets/bia@empresa.com",
        json={"month": month, "limit_brl": 321},
        headers={"Authorization": "Bearer tok-gestor"},
    )
    assert put.status_code == 200
    assert json.loads(nexo("--json", "budget", "show")[1])["limit_brl"] == 321.0
    run_task(project, nexo, scenario)
    invoice = tmp_path / "fatura.csv"
    invoice.write_text(f"tool,month,amount_brl\nclaude,{month},5.00\ncopilot,{month},405.00\n", encoding="utf-8")
    credentials.set_secret("collector", "tok-finops")
    tokens = credentials.get_secret(f"central:{device}")
    credentials.delete_secret(f"central:{device}")  # conciliação é feita pelo time de FinOps (token próprio)
    result = {r["tool"]: r for r in json.loads(nexo("--json", "cost", "reconcile", "--invoice", str(invoice))[1])}
    assert result["claude"]["measured_brl"] == 5.4 and result["claude"]["divergent"] is True  # +8%
    assert result["copilot"]["measured_brl"] == 0.0
    credentials.set_secret(f"central:{device}", tokens or "")
    admin = {"Authorization": "Bearer tok-admin"}
    from nexo.central.app import db as collector_db
    from nexo.central.app import pseudonym

    pid = pseudonym("bia@empresa.com")
    assert central.get(f"/v2/admin/reidentify/{pid}", params={"reason": "curto"}, headers=admin).status_code == 400
    who = central.get(f"/v2/admin/reidentify/{pid}", params={"reason": "incidente INC-42 de segurança"}, headers=admin)
    assert who.json()["dev"] == "bia@empresa.com"
    assert central.post("/v2/admin/purge", json={"older_than_days": 0}, headers=admin).json()["deleted"] > 0
    assert int(collector_db().query("SELECT COUNT(*) AS n FROM fact_step")[0]["n"]) > 0  # fatos agregados ficam
    actions = {r["action"] for r in collector_db().query("SELECT action FROM audit")}
    assert {"device.approved", "budget.set", "identity.reidentified", "retention.purge"} <= actions


def test_anchor_detects_rewritten_local_log(central: Any) -> None:
    headers = {"Authorization": "Bearer tok-sso-bia"}
    anchor = {"device_id": "d1", "seq": 7, "hash": "a" * 64, "at": "2026-09-25"}
    assert central.post("/v2/anchors", json=anchor, headers=headers).status_code == 200
    assert central.post("/v2/anchors", json={**anchor, "hash": "b" * 64}, headers=headers).status_code == 409

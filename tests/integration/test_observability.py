"""observabilidade e segurança avançadas: traces OTLP, replay, relatório de falha, frota, alerta de custo em
tempo real, SIEM, DORA/ROI, GitHub App e Service Principal, commits assinados, modo isolado e etapas de qualidade."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from nexo.kernel.monitoring.otlp import Span, to_otlp
from nexo.kernel.monitoring.telemetry import CONTRACT
from nexo.kernel.runtime import http
from tests.conftest import DEFAULT_TASK, Runner, dump_yaml, edit, git_cmd
from tests.fakes.exe import fake_exe
from tests.fakes.http_fake import Response
from tests.integration.test_story import run_json
from tests.integration.test_telemetry import TOKENS, enable, login

DONE = {"progress": {"percent": 100}}
OTLP = "https://otel.empresa.com"


def task(project: Path, tid: str = "T-1") -> None:
    dump_yaml(project / ".nexo" / "tasks" / f"{tid}.yaml", DEFAULT_TASK)


class Capture:
    """Transporte HTTP que captura chamadas a hosts de teste e repassa o resto (ex.: FakeCloud)."""

    def __init__(self, fallback: Any = None) -> None:
        self.calls: list[tuple[str, str, dict[str, str], Any]] = []
        self.fallback = fallback

    def __call__(self, method: str, url: str, headers: dict[str, str], data: bytes | None) -> Response:
        body = None
        if data:
            try:
                body = json.loads(data)
            except ValueError:
                body = data.decode()
        self.calls.append((method, url, headers, body))
        if url.startswith(OTLP):
            return Response(200, {})
        if self.fallback is not None:
            return self.fallback(method, url, headers, data)  # type: ignore[no-any-return]
        return Response(404, {"message": "sem rota"})


def org(nexo_home: Path, data: dict[str, Any]) -> None:
    path = nexo_home / "brain" / "org" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    dump_yaml(path, data)
    path.write_text(existing + path.read_text(encoding="utf-8"), encoding="utf-8")


# ---------------------------------------------------------------- O-01 traces


def test_otlp_encoding() -> None:
    parent = Span("nexo run", "a" * 32, "b" * 16, start_ns=1, end_ns=5)
    child = Span("nexo.agent.step", "a" * 32, "c" * 16, parent_id="b" * 16, start_ns=2, end_ns=4)
    child.set(tool="claude", model="sonnet", path="/Users/x/segredo.py", n=3, ok=True, ratio=0.5)
    child.event("tool_call", tool="Write")
    body = to_otlp([parent, child], {"service.name": "nexo-cli"})
    spans = body["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert spans[1]["parentSpanId"] == "b" * 16 and spans[1]["endTimeUnixNano"] == "4"
    attrs = {a["key"]: a["value"] for a in spans[1]["attributes"]}
    assert attrs["tool"] == {"stringValue": "claude"} and attrs["n"] == {"intValue": "3"}
    assert "path" not in attrs  # caminho nunca sai
    assert spans[1]["events"][0]["name"] == "tool_call"


def test_command_trace_spooled_and_exported(project: Path, nexo: Runner, scenario, nexo_home: Path) -> None:  # type: ignore[no-untyped-def]
    org(
        nexo_home,
        {
            "observability": {"otlp": {"endpoint": OTLP, "service_name": "nexo-cli"}},
            "telemetry": {"background": False},
        },
    )  # sem processo em segundo plano disputando o spool
    nexo("config", "set", "observability.otlp.endpoint", "https://desvio.example.com")  # pessoal: ignorado
    task(project)
    scenario([{"tool": "claude", "events": [edit(), {"tool": {"name": "Bash", "input": {"command": "ls"}}}, DONE]}])
    assert nexo("run", "task", "T-1")[0] == 0
    spool = [json.loads(line) for line in (nexo_home / "otlp-spool.jsonl").read_text(encoding="utf-8").splitlines()]
    by = {s["name"]: s for s in spool}
    assert {"nexo run", "nexo.run.task", "nexo.agent.step"} <= set(by)
    step, run = by["nexo.agent.step"], by["nexo.run.task"]
    assert step["parent_id"] == run["span_id"] and run["parent_id"] == by["nexo run"]["span_id"]
    assert step["attrs"]["tool"] == "claude" and [e["name"] for e in step["events"]].count("tool_call") == 2
    assert str(project) not in json.dumps(spool)
    cap = Capture()
    http.set_transport(cap)
    try:
        code, out, _ = nexo("--json", "telemetry", "flush")
    finally:
        http.set_transport(None)
    assert code == 0 and json.loads(out)["spans"]["exported"] >= 3
    method, url, _, body = cap.calls[0]
    assert url == f"{OTLP}/v1/traces" and body["resourceSpans"][0]["resource"]["attributes"][0]["key"] == "os.type"
    left = [
        json.loads(line)["name"] for line in (nexo_home / "otlp-spool.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert left == ["nexo telemetry"]  # só o span do próprio envio fica para a próxima vez


# ---------------------------------------------------------------- O-02 replay


def test_redacted_replay_is_audited_and_purged(project: Path, nexo: Runner, scenario, nexo_home: Path) -> None:  # type: ignore[no-untyped-def]
    task(project)
    secret = "ghp_" + "a1B2" * 9
    scenario([{"tool": "claude", "events": [{"message": {"text": f"usando o token {secret}"}}, edit(), DONE]}])
    code, rep = run_json(nexo, "run", "task", "T-1")
    run_id = rep["run_id"]
    assert nexo("logs", "--trace", run_id)[0] != 0  # sem motivo
    code, out, _ = nexo("--json", "logs", "--trace", run_id, "--reason", "investigando o incidente INC-42")
    steps = json.loads(out)
    kinds = [e["kind"] for e in steps[0]["events"]]
    assert "message" in kinds and "done" in kinds and steps[0]["tool"] == "claude"
    assert secret not in out and "usando o token" in out
    logs = json.loads(nexo("--json", "logs", "-n", "50")[1])
    assert any(e["type"] == "transcript.viewed" for e in logs)
    old = nexo_home / "transcripts" / run_id
    past = time.time() - 91 * 86400
    os.utime(old, (past, past))
    from nexo.kernel.context import AppContext
    from nexo.kernel.platform import observability

    assert observability.purge_transcripts(AppContext(cwd=project, home=nexo_home)) == 1


# ---------------------------------------------------------------- O-05 relatório de falha


def test_unexpected_error_becomes_sanitized_crash_report(  # type: ignore[no-untyped-def]
    project: Path, nexo: Runner, nexo_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexo.modules.execution.app import status as execution_status

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError(f"falhou em {project}/segredo.py com senha=hunter2 token ghp_{'x1Y2' * 9}")

    monkeypatch.setattr(execution_status, "overview", boom)
    code, _, err = nexo("status")
    assert code == 1 and "erro inesperado (RuntimeError)" in err
    report = json.loads(next((nexo_home / "crash").glob("*.json")).read_text(encoding="utf-8"))
    assert report["type"] == "RuntimeError" and report["command"] == "status"
    assert all(":" in f and "/" not in f for f in report["frames"]) and report["frames"]
    assert str(project) not in json.dumps(report) and "ghp_" not in report["message"]
    logs = json.loads(nexo("--json", "logs", "-n", "20")[1])
    assert any(e["type"] == "nexo.crash" for e in logs)


# ---------------------------------------------------------------- O-06 frota


def test_fleet_page_shows_versions_and_drift(  # type: ignore[no-untyped-def]
    project: Path, nexo: Runner, scenario, nexo_home: Path, central: Any
) -> None:
    enable(nexo_home)
    login(nexo_home, central, "tok-sso-bia")
    task(project)
    scenario([{"tool": "claude", "events": [edit(), DONE]}])
    nexo("run", "task", "T-1")
    from nexo.central.common import db

    fleet = db().query("SELECT * FROM fleet")
    assert len(fleet) == 1 and fleet[0]["nexo"] and "claude" in json.loads(fleet[0]["agents"])
    db().execute(
        "INSERT INTO fleet (device_id, dev_pid, squad, nexo, brain, agents, os, last_seen) VALUES "
        "('old-device', 'x', '', '0.0.1', '', '{}', 'linux', ?)",
        (time.time(),),
    )
    page = central.get("/v2/pages/fleet", headers={"Authorization": "Bearer tok-admin"}).json()
    k = {x["id"]: x["value"] for x in page["kpis"]}
    assert k["devices"] == 2 and k["on_latest_nexo"] == 50.0
    drift = next(t for t in page["tables"] if t["id"] == "devices")["rows"]
    assert [r[0] for r in drift] == ["old-devi"]
    assert central.get("/v2/pages/fleet", headers={"Authorization": "Bearer tok-sso-bia"}).status_code == 403


# ---------------------------------------------------------------- O-04 alerta de custo e S-06 SIEM


def _step(cost: float, calls: int = 3) -> dict[str, Any]:
    at = datetime.now(UTC).isoformat()
    return {
        "event_id": uuid.uuid4().hex,
        "kind": "step",
        "seq": 0,
        "type": "step",
        "at": at,
        "payload": {
            "step": uuid.uuid4().hex,
            "run": "r",
            "role": "developer",
            "tool": "claude",
            "model": "sonnet",
            "started_at": at,
            "usage_brl": cost,
            "outcome": "success",
            "tool_calls": calls,
        },
    }


def _post(central: Any, events: list[dict[str, Any]], token: str = "tok-sso-bia") -> None:
    body = {"contract": CONTRACT, "device_id": "", "project": "p1", "context": {"squad": "sq-a"}, "events": events}
    resp = central.post("/v2/events", json=body, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text


def test_realtime_cost_and_loop_alerts(central: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexo.central import distribution
    from nexo.central.common import db

    sent: list[Any] = []
    monkeypatch.setattr(distribution, "transport", lambda url, body, headers: sent.append((url, body)) or (200, {}))
    monkeypatch.setenv("NX_COLLECTOR_TEAMS_WEBHOOKS", json.dumps({"nexo": "https://teams/nexo"}))
    _post(central, [_step(1.0) for _ in range(20)])
    assert db().query("SELECT * FROM insights WHERE detector LIKE 'RT-%'") == []
    _post(central, [_step(12.0)])  # 12× a média
    _post(central, [_step(0.5, calls=400)])  # loop
    rows = {r["detector"]: r for r in db().query("SELECT * FROM insights WHERE detector LIKE 'RT-%'")}
    assert set(rows) == {"RT-01", "RT-02"} and json.loads(rows["RT-01"]["evidence"])["factor"] == 5.0
    assert [u for u, _ in sent] == ["https://teams/nexo", "https://teams/nexo"]
    assert distribution.validate_card(sent[0][1]) == []


def test_security_events_reach_the_siem(central: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexo.central import distribution
    from nexo.central.common import db

    sent: list[Any] = []
    state = {"down": True}

    def transport(url: str, body: Any, headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        if state["down"]:
            raise OSError("SIEM fora do ar")
        sent.append((url, body, headers))
        return 200, {}

    monkeypatch.setattr(distribution, "transport", transport)
    monkeypatch.setenv("NX_COLLECTOR_SIEM_URL", "https://siem.empresa.com/nexo")
    monkeypatch.setenv("NX_COLLECTOR_SIEM_TOKEN", "siem-tok")
    violation = {
        "event_id": "v1",
        "kind": "event",
        "seq": 1,
        "type": "policy.violation",
        "at": "2026-09-25T10:00:00",
        "payload": {"rule": "deny_command", "task": "T-1"},
    }
    _post(central, [violation])  # SIEM fora: fica na fila
    assert db().one("SELECT COUNT(*) AS n FROM siem_outbox WHERE status = 'pending'")["n"] >= 1
    state["down"] = False
    central.put("/v2/admin/kill-switch", json={"tools": ["devin"]}, headers={"Authorization": "Bearer tok-admin"})
    from nexo.central import siem

    assert siem.flush(db())["pending"] == 0
    events = [e for _, body, _ in sent for e in body]
    by = {e["action"]: e for e in events}
    assert by["policy.violation"]["user"] == "bia@empresa.com" and by["policy.violation"]["severity"] == "alta"
    assert by["killswitch.set"]["user"] == "ana.admin" and sent[0][2]["Authorization"] == "Bearer siem-tok"
    views = db().query("SELECT detail FROM audit WHERE action = 'identity.view' AND actor = 'siem'")
    assert views  # reidentificação para o SIEM fica auditada
    sent.clear()
    monkeypatch.setenv("NX_COLLECTOR_SIEM_FORMAT", "splunk-hec")
    _post(central, [{**violation, "event_id": "v2"}])
    assert sent and sent[0][2]["Authorization"] == "Splunk siem-tok" and sent[0][1]["sourcetype"] == "nexo:security"


# ---------------------------------------------------------------- I-17..19 DORA, ROI e incidentes


def test_dora_roi_and_nexo_incidents(central: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "NX_COLLECTOR_TOKENS",
        json.dumps(
            {
                **TOKENS,
                "tok-ci": {"role": "integration", "dev": "pipeline"},
                "tok-dir": {"role": "director", "dev": "dir"},
            }
        ),
    )
    now = datetime.now(UTC)
    h = {"Authorization": "Bearer tok-ci"}

    def iso(days: float) -> str:
        return (now - timedelta(days=days)).isoformat()

    for n, (status, nexo_commit) in enumerate(
        [("success", True), ("success", False), ("failure", False), ("success", True)]
    ):
        body = {
            "id": f"d{n}",
            "service": "pagamentos",
            "env": "production",
            "status": status,
            "finished_at": iso(1 + n),
            "commits": [{"sha": f"{n}abc{'0' * 36}", "authored_at": iso(3 + n), "nexo": nexo_commit}],
        }
        assert central.post("/v2/deployments", json=body, headers=h).status_code == 200
    inc = {
        "id": "INC-1",
        "service": "pagamentos",
        "opened_at": iso(0.5),
        "resolved_at": iso(0.25),
        "severity": "2",
        "cause_sha": "0abc",
    }
    assert central.post("/v2/incidents", json=inc, headers=h).json()["nexo_caused"] == 1
    assert central.get("/v2/pages/executive", headers=h).status_code == 403  # integração não lê o dashboard
    assert (
        central.post("/v2/deployments", json=body, headers={"Authorization": "Bearer tok-sso-bia"}).status_code == 403
    )
    page = central.get("/v2/pages/dora", headers={"Authorization": "Bearer tok-dir"}).json()
    k = {x["id"]: x["value"] for x in page["kpis"]}
    assert k["lead_time_change"] == 48.0 and k["change_failure"] == 50.0  # d2 falhou + d0 causou incidente
    assert k["mttr"] == 6.0 and k["nexo_incidents"] == 1 and k["deploy_freq"] > 0
    assert page["tables"][0]["rows"][0][:2] == ["pagamentos", 3]


# ---------------------------------------------------------------- S-07 GitHub App e Service Principal


# ---------------------------------------------------------------- S-09 commits assinados


@pytest.mark.skipif(shutil.which("ssh-keygen") is None, reason="ssh-keygen indisponível")
def test_nexo_commits_are_signed_with_service_key(  # type: ignore[no-untyped-def]
    project: Path, nexo: Runner, scenario, tmp_path: Path
) -> None:
    key = tmp_path / "nexo_ed25519"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "nexo-bot", "-f", str(key)], check=True)
    nexo("config", "set", "git.signing.key", str(key), "--project")
    nexo("config", "set", "git.identity.name", "Nexo Bot", "--project")
    nexo("config", "set", "git.identity.email", "nexo-bot@empresa.com", "--project")
    task(project)
    scenario([{"tool": "claude", "events": [edit(), DONE]}])
    code, rep = run_json(nexo, "run", "task", "T-1")
    assert rep["state"] == "Concluido"
    wt = Path(rep["worktree"])
    allowed = tmp_path / "allowed"
    allowed.write_text(
        f"nexo-bot@empresa.com {(tmp_path / 'nexo_ed25519.pub').read_text(encoding='utf-8')}", encoding="utf-8"
    )
    status = git_cmd(wt, "-c", f"gpg.ssh.allowedSignersFile={allowed}", "log", "-1", "--format=%G?|%an|%B")
    assert status.startswith("G|Nexo Bot|") and "Nexo-Responsible: os:" in status
    from nexo.kernel.workspace import git

    git.configure()  # não vaza a configuração para os próximos testes


# ---------------------------------------------------------------- S-04 modo isolado


def test_isolation_required_and_container_command(  # type: ignore[no-untyped-def]
    project: Path, nexo: Runner, scenario, nexo_home: Path, tmp_path: Path
) -> None:
    org(nexo_home, {"isolation": {"required_for": ["confidential"]}})
    task(project)
    scenario([{"tool": "claude", "events": [edit(), DONE]}])
    code, out, err = nexo("run", "task", "T-1")
    assert code != 0 and "exigem o modo isolado" in out + err
    code, out, _ = nexo("--json", "isolation", "setup")
    data = json.loads(out)
    assert "api.anthropic.com" in data["domains"] and "pypi.org" in data["domains"]
    squid = (Path(data["folder"]) / "squid.conf").read_text(encoding="utf-8")
    assert "http_access deny all" in squid and "internal: true" in (
        Path(data["folder"]) / "egress.compose.yml"
    ).read_text(encoding="utf-8")
    # a sessão do agente embrulha a CLI no contêiner, com o ambiente por nome (nunca o valor na linha de comando)
    from nexo.kernel.models.agent import AgentRequest
    from nexo.modules.execution.adapters.agents.base import Patterns, SubprocessSession
    from nexo.modules.execution.adapters.agents.claude import ClaudeParser

    record = tmp_path / "args.json"
    engine = fake_exe(
        tmp_path / "bin", "fake-docker", f"import json, sys\njson.dump(sys.argv[1:], open({str(record)!r}, 'w'))\n"
    )
    req = AgentRequest(
        "developer",
        "p",
        str(project),
        "sonnet",
        env={"NX_TASK_ID": "T-9"},
        container={"image": "empresa/nexo-agent:1", "engine": str(engine)},
    )
    session = SubprocessSession(["claude", "-p"], req, ClaudeParser(Patterns({})), stdin_text="p")
    list(session.events())
    args = json.loads(record.read_text(encoding="utf-8"))
    assert args[:4] == ["run", "--rm", "-i", "--network"] and "--cap-drop=ALL" in args
    assert "empresa/nexo-agent:1" in args and args[-2:] == ["claude", "-p"]
    assert "NX_TASK_ID" in args and not any("T-9" in a for a in args)


# ---------------------------------------------------------------- S-10 / Q-01 etapas de qualidade


def test_mutation_score_parsers() -> None:
    from nexo.modules.quality.adapters.parsers import mutation_score

    assert mutation_score({"killed": 8, "survived": 2, "timeout": 0}) == 0.8
    stryker = {
        "files": {
            "a.ts": {
                "mutants": [{"status": "Killed"}, {"status": "Survived"}, {"status": "Ignored"}, {"status": "Timeout"}]
            }
        }
    }
    assert mutation_score(stryker) == pytest.approx(2 / 3)
    assert mutation_score({"killed": 0, "survived": 0}) is None


def test_optional_security_and_mutation_stages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexo.modules.quality.adapters.runner import run_stage

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sarif = {
        "runs": [
            {
                "tool": {"driver": {"name": "trivy"}},
                "results": [
                    {
                        "ruleId": "CVE-1",
                        "level": "error",
                        "message": {"text": "lib vulnerável"},
                        "locations": [{"physicalLocation": {"artifactLocation": {"uri": "Dockerfile"}}}],
                    }
                ],
            }
        ]
    }
    fake_exe(
        bin_dir,
        "trivy",
        f"import json, sys\na = sys.argv\njson.dump({sarif!r}, open(a[a.index('--output') + 1], 'w'))\n",
    )
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    work = tmp_path / "w"
    work.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    stage = {
        "id": "container",
        "kind": "security",
        "tool": "trivy",
        "when_exists": "Dockerfile",
        "run": ["trivy", "fs", "--format", "sarif", "--output", "{out}/trivy.sarif", "."],
        "parser": "sarif",
        "report": "{out}/trivy.sarif",
        "default_severity": "high",
        "required": False,
    }
    subs = {"out": str(out), "min_mutation_score": "0.8", "dast_url": ""}
    assert run_stage(stage, work, out, subs).status == "skipped"  # sem Dockerfile
    (work / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
    result = run_stage(stage, work, out, subs)
    assert result.status == "failed" and result.findings[0].rule == "CVE-1"
    dast = {"id": "dast", "tool": "trivy", "when_param": "dast_url", "run": ["trivy"], "required": False}
    assert "dast_url" in run_stage(dast, work, out, subs).summary
    (work / "stats.json").write_text(json.dumps({"killed": 6, "survived": 4}), encoding="utf-8")
    mutation = {
        "id": "mutation",
        "kind": "mutation",
        "tool": "trivy",
        "run": ["trivy", "--output", "x"],
        "parser": "mutation",
        "report": str(work / "stats.json"),
        "min_score": "{min_mutation_score}",
        "required": False,
    }
    result = run_stage(mutation, work, out, subs)
    assert result.status == "failed" and result.mutation_score == 0.6 and "60%" in result.summary


def test_collector_request_traces(central: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexo.central import distribution, tracing

    sent: list[Any] = []
    monkeypatch.setattr(distribution, "transport", lambda url, body, headers: sent.append((url, body)) or (200, {}))
    monkeypatch.setenv("NX_COLLECTOR_OTLP_ENDPOINT", "https://otel.empresa.com")
    central.get("/v2/pages/executive", headers={"Authorization": "Bearer tok-admin"})
    assert tracing.flush() >= 1
    spans = sent[0][1]["resourceSpans"][0]["scopeSpans"][0]["spans"]
    assert any(s["name"] == "GET /v2/pages/{name}" for s in spans)
    assert "tok-admin" not in json.dumps(sent)

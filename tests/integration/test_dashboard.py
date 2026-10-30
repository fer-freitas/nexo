"""modelo de fatos, dashboard com visibilidade no servidor, drill-down, export BI, SSO, distribuição e CLI.

Coletor testado com SQLite e PostgreSQL."""

from __future__ import annotations

import io
import json
import re
import time
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from nexo.kernel.monitoring.telemetry import CONTRACT
from tests.conftest import Runner
from tests.integration.test_story_cost import STEPS
from tests.integration.test_telemetry import TOKENS, enable, login

PEOPLE = {
    "tok-bia": {"role": "dev", "dev": "bia"},
    "tok-caio": {"role": "dev", "dev": "caio"},
    "tok-lia": {"role": "lead", "dev": "lia"},
    "tok-gil": {"role": "manager", "dev": "gil"},
    "tok-dir": {"role": "director", "dev": "dir"},
    "tok-fin": {"role": "finops", "dev": "fin"},
    "tok-nx": {"role": "nexo", "dev": "nx"},
    "tok-sec": {"role": "security", "dev": "sec"},
}
ROLE_TOKEN = {v["role"]: k for k, v in PEOPLE.items() if k != "tok-caio"} | {"admin": "tok-admin"}
DIRECTORY = {
    "directorates": [
        {
            "id": "dir-digital",
            "name": "Digital",
            "squads": [
                {"id": "sq-a", "leads": ["lia"], "managers": ["gil"], "members": ["bia"]},
                {"id": "sq-b", "leads": ["rui"], "members": ["caio"]},
            ],
        }
    ],
    "roles": {"lia": "lead", "gil": "manager", "dir": "director", "fin": "finops", "nx": "nexo", "sec": "security"},
}


@pytest.fixture
def people(central: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("NX_COLLECTOR_TOKENS", json.dumps({**TOKENS, **PEOPLE}))
    return central


def h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def now_iso(delta_days: float = 0) -> str:
    """Instante relativo a uma referência sempre dentro do mês atual: no início do mês, "12 horas atrás"
    cairia no mês anterior e as páginas do mês ficariam zeradas."""
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    ref = max(now, month_start + timedelta(days=2))
    return (ref - timedelta(days=delta_days)).isoformat(timespec="milliseconds")


def rec(kind: str, type_: str, payload: dict[str, Any], at: str | None = None) -> dict[str, Any]:
    return {
        "event_id": uuid.uuid4().hex,
        "kind": kind,
        "seq": 0,
        "type": type_,
        "at": at or now_iso(),
        "payload": payload,
    }


def step(story: str, tool: str = "claude", brl: float = 5.4, outcome: str = "success") -> dict[str, Any]:
    at = now_iso(0.5)
    return rec(
        "step",
        "step",
        {
            "step": uuid.uuid4().hex,
            "run": "r1",
            "target": story,
            "story": story,
            "role": "developer",
            "tool": tool,
            "model": "sonnet",
            "started_at": at,
            "seconds": 30.0,
            "outcome": outcome,
            "usage_brl": brl,
            "api_equiv_usd": brl / 5.4,
            "acu": 2.0 if tool == "devin" else None,
            "brain_version": "1.2.0",
        },
        at,
    )


def story_snap(story: str, state: str = "Concluido", cost: float = 10.8, **extra: Any) -> dict[str, Any]:
    done = now_iso(0.2) if state == "Concluido" else None
    return rec(
        "story",
        "story",
        {
            "story": story,
            "feature": "F-1",
            "stack": "python",
            "points": 2,
            "state": state,
            "human_gate": False,
            "escalated": False,
            "bugs": 0,
            "attempts": 1,
            "spec_returns": 0,
            "clarifications": 0,
            "handoffs": 0,
            "cost_brl": cost,
            "cap_brl": 45.0,
            "estimate_brl": 10.0,
            "started_at": now_iso(1),
            "done_at": done,
            "waiting_human_hours": 0.0,
            **extra,
        },
    )


def post(client: Any, token: str, squad: str, events: list[dict[str, Any]], project: str = "p1") -> dict[str, Any]:
    body = {
        "contract": CONTRACT,
        "device_id": "",
        "project": project,
        "context": {"squad": squad, "stack": "python"},
        "events": events,
    }
    resp = client.post("/v2/events", json=body, headers=h(token))
    assert resp.status_code == 200, resp.text
    return dict(resp.json())


def seed(client: Any) -> None:
    assert client.put("/v2/admin/directory", json=DIRECTORY, headers=h("tok-admin")).status_code == 200
    post(
        client,
        "tok-bia",
        "sq-a",
        [
            step("US-1"),
            step("US-1"),
            step("US-2", tool="devin", brl=12.0),
            story_snap("US-1"),
            story_snap("US-2", state="AguardandoHumano", cost=12.0),
            rec(
                "event",
                "gate.decision",
                {"gate": "code", "story": "US-1", "decision": "auto", "score": 0.95, "threshold": 0.9},
            ),
            rec(
                "event",
                "quality.run",
                {
                    "story": "US-1",
                    "stack": "python",
                    "stages": [["unit", "passed"]],
                    "failed": 0,
                    "coverage": 91.0,
                    "findings": {"high": 1},
                },
            ),
            rec("event", "run.blocked", {"task": "T-1-1", "kind": "budget"}),
            rec("event", "policy.violation", {"rule": "deny_command", "task": "T-1-1"}),
        ],
    )
    post(client, "tok-caio", "sq-b", [step("US-7", brl=3.0), story_snap("US-7", cost=3.0)], project="p2")
    month = datetime.now(UTC).strftime("%Y-%m")
    for dev, limit in (("bia", 100.0), ("caio", 50.0)):
        client.put(f"/v1/budgets/{dev}", json={"month": month, "limit_brl": limit}, headers=h("tok-admin"))
    client.put(
        "/v2/admin/licenses", json=[{"dev": "bia", "tool": "devin", "monthly_brl": 972.0}], headers=h("tok-admin")
    )


# ---------------------------------------------------------------- 11.1 modelo de fatos


def test_story_run_feeds_the_central_fact_model(  # type: ignore[no-untyped-def]
    planned, project: Path, nexo: Runner, scenario, nexo_home: Path, central: Any
) -> None:
    enable(nexo_home)
    login(nexo_home, central, "tok-sso-bia")
    assert nexo("config", "set", "squad", "sq-a", "--project")[0] == 0
    planned(scenario, STEPS)
    assert nexo("run", "story", "US-1-1")[0] == 0
    code, out, err = nexo("--json", "telemetry", "flush")
    assert code == 0 and json.loads(out)["rejected"] == 0, out + err
    from nexo.central import facts
    from nexo.central.common import db

    d = db()
    fs = d.one("SELECT * FROM fact_story WHERE story = 'US-1-1'")
    assert fs is not None and fs["state"] == "Concluido" and fs["squad"] == "sq-a" and fs["feature"] == "F-1"
    assert fs["cost_brl"] == 16.2 and fs["cap_brl"] == 45.0 and fs["done_at"] and fs["points"] >= 1
    steps = d.query("SELECT target, story, usage_brl FROM fact_step WHERE squad = 'sq-a' AND story IS NOT NULL")
    assert {s["target"] for s in steps} >= {"T-1-1", "T-1-2"}  # passos de tarefa ligados à história
    assert {s["story"] for s in steps} == {"US-1-1"}
    planning = d.query("SELECT target FROM fact_step WHERE story IS NULL")
    assert {s["target"] for s in planning} <= {"F-1"}  # só o planejamento fica sem história
    assert round(sum(float(s["usage_brl"]) for s in steps), 2) == 16.2
    assert d.one("SELECT decision FROM fact_gate WHERE gate = 'code' AND story = 'US-1-1'")["decision"] == "auto"
    assert d.one("SELECT COUNT(*) AS n FROM fact_quality WHERE story = 'US-1-1'")["n"] >= 1
    before = facts.fingerprint(d)
    resp = central.post("/v2/admin/reprocess", headers=h("tok-admin"))
    assert resp.status_code == 200 and resp.json()["fingerprint"] == before  # reprocessar = mesmo resultado


def test_consolidation_is_idempotent_and_order_independent(people: Any) -> None:
    from nexo.central import facts
    from nexo.central.common import db

    older = story_snap("US-5", state="EmDev", cost=1.0)
    newer = story_snap("US-5", state="Concluido", cost=9.0)
    newer["at"] = now_iso(-0.01)
    post(people, "tok-bia", "sq-a", [newer, step("US-5")])
    post(people, "tok-bia", "sq-a", [older])  # chega depois, mas é mais antigo: não sobrescreve
    again = post(people, "tok-bia", "sq-a", [newer])
    assert again["duplicate"] == [newer["event_id"]]
    d = db()
    row = d.one("SELECT state, cost_brl FROM fact_story WHERE story = 'US-5'")
    assert (row["state"], row["cost_brl"]) == ("Concluido", 9.0)
    first = facts.fingerprint(d)
    assert facts.reprocess(d) == 3 and facts.fingerprint(d) == first


# ---------------------------------------------------------------- 11.2/11.3 páginas × perfis


def test_every_profile_on_every_page_follows_the_matrix(people: Any) -> None:
    from nexo.central.visibility import PAGES

    seed(people)
    for role, token in ROLE_TOKEN.items():
        for page, (_, roles) in PAGES.items():
            resp = people.get(f"/v2/pages/{page}", headers=h(token))
            assert resp.status_code == (200 if role in roles else 403), (role, page, resp.text)
    squads = {
        role: people.get("/v2/pages/squad", headers=h(t)).json().get("squads")
        for role, t in ROLE_TOKEN.items()
        if role in ("dev", "lead", "manager", "director")
    }
    assert squads == {"dev": ["sq-a"], "lead": ["sq-a"], "manager": ["sq-a"], "director": None}
    assert people.get("/v2/pages/squad?squad=sq-b", headers=h("tok-bia")).status_code == 403
    assert people.get("/v2/pages/squad?directorate=dir-digital", headers=h("tok-dir")).json()["squads"] == [
        "sq-a",
        "sq-b",
    ]


def _individual(page: dict[str, Any]) -> dict[str, list[list[Any]]]:
    return {t["id"]: t["rows"] for t in page["tables"] if t["individual"]}


def test_individual_data_only_where_the_matrix_allows(people: Any) -> None:
    from nexo.central.common import db

    seed(people)
    assert _individual(people.get("/v2/pages/squad", headers=h("tok-bia")).json()) == {}
    lead = _individual(people.get("/v2/pages/squad", headers=h("tok-lia")).json())
    assert [r[0] for r in lead["people_blocks"]] == ["bia"] and lead["people_blocks"][0][1:] == [1, 1]
    assert [r[0] for r in lead["squad_budget_people"]] == ["bia"]  # só o próprio squad; caio (sq-b) não aparece
    manager = _individual(people.get("/v2/pages/finops", headers=h("tok-gil")).json())
    assert list(manager) == ["finops_budget_people"] and manager["finops_budget_people"][0][:2] == ["bia", 100.0]
    finops = _individual(people.get("/v2/pages/finops", headers=h("tok-fin")).json())
    assert list(finops) == ["licenses"] and finops["licenses"][0][:3] == ["bia", "devin", 972.0]
    assert _individual(people.get("/v2/pages/finops", headers=h("tok-dir")).json()) == {}
    assert _individual(people.get("/v2/pages/quality", headers=h("tok-lia")).json()) == {}
    assert _individual(people.get("/v2/pages/quality", headers=h("tok-sec")).json()) == {
        "violations_people": [["bia", 1]]
    }
    assert _individual(people.get("/v2/pages/executive", headers=h("tok-nx")).json()) == {}
    views = db().query("SELECT actor, detail FROM audit WHERE action = 'identity.view'")
    assert {v["actor"] for v in views} == {"lia", "gil", "fin", "sec"}  # toda reidentificação auditada


def test_pages_numbers_and_org_reference(people: Any) -> None:
    seed(people)
    ex = people.get("/v2/pages/executive", headers=h("tok-dir")).json()
    k = {x["id"]: x["value"] for x in ex["kpis"]}
    assert k["cost_month"] == 25.8 and k["budget_month"] == 150.0 and k["done"] == 2 and k["fpy"] == 100.0
    assert k["brl_per_point"] == round((10.8 + 3.0) / 4, 2) and ex["org"] == []
    lead = people.get("/v2/pages/executive", headers=h("tok-lia")).json()
    kl = {x["id"]: x["value"] for x in lead["kpis"]}
    assert kl["cost_month"] == 22.8 and kl["budget_month"] == 100.0  # só sq-a
    assert {x["id"]: x["value"] for x in lead["org"]}["org_cost"] == 25.8  # referência da org
    fin = {x["id"]: x["value"] for x in people.get("/v2/pages/finops", headers=h("tok-fin")).json()["kpis"]}
    assert (
        fin["acu_used"] == 2.0 and fin["acu_included"] == 90.0 and fin["fx"] == 5.4
    )  # R$ 972 ÷ R$ 10,80 (tabela pública)
    me = people.get("/v2/pages/me", headers=h("tok-bia")).json()
    km = {x["id"]: x["value"] for x in me["kpis"]}
    assert km["budget"] == 100.0 and km["used"] == 22.8 and km["pending"] == 1 and km["acu_included"] == 90.0
    assert [s[1] for s in next(t for t in me["tables"] if t["id"] == "stories")["rows"]] == ["US-1", "US-2"]
    routing = people.get("/v2/pages/routing", headers=h("tok-nx")).json()
    assert {r[0] for r in routing["tables"][0]["rows"]} == {"claude", "devin"}
    brain = {x["id"]: x["value"] for x in people.get("/v2/pages/brain", headers=h("tok-nx")).json()["kpis"]}
    assert brain["latest"] == "1.2.0" and brain["adoption"] == 100.0


def test_drill_down_reaches_origin_ids_within_scope(people: Any) -> None:
    seed(people)
    ex = people.get("/v2/pages/executive", headers=h("tok-dir")).json()
    drill = next(x for x in ex["kpis"] if x["id"] == "done")["drill"]
    rows = people.get("/v2/drill", params=drill, headers=h("tok-dir")).json()["rows"]
    assert sorted(r[1] for r in rows) == ["US-1", "US-7"]
    rows = people.get("/v2/drill", params=drill, headers=h("tok-lia")).json()["rows"]
    assert [r[1] for r in rows] == ["US-1"]  # tech lead só vê o próprio squad
    steps = people.get("/v2/drill", params={"source": "step", "story": "US-1"}, headers=h("tok-dir")).json()
    assert steps["columns"][:4] == ["project", "story", "run", "step"] and len(steps["rows"]) == 2
    assert "dev_pid" not in json.dumps(steps)
    assert people.get("/v2/drill", params={"source": "story"}, headers=h("tok-bia")).status_code == 200
    assert people.get("/v2/drill", params={"source": "identities"}, headers=h("tok-dir")).status_code == 404


# ---------------------------------------------------------------- 11.6 export BI


def test_exports_apply_visibility_and_never_carry_people(people: Any) -> None:
    import pyarrow.parquet as pq

    seed(people)
    assert people.get("/v2/export/fact_step", headers=h("tok-bia")).status_code == 403
    csv_dir = people.get("/v2/export/fact_story", headers=h("tok-dir")).text
    header = csv_dir.splitlines()[0].split(",")
    assert "dev_pid" not in header and "story" in header and len(csv_dir.splitlines()) == 4
    lead = people.get("/v2/export/fact_story", headers=h("tok-lia")).text
    assert "US-1" in lead and "US-7" not in lead
    for fact in ("fact_step", "fact_gate", "fact_quality", "dim_squad"):
        resp = people.get(f"/v2/export/{fact}", headers=h("tok-dir"))
        assert resp.status_code == 200 and "dev" not in resp.text.splitlines()[0].split(","), fact
    parquet = people.get("/v2/export/fact_step?format=parquet", headers=h("tok-fin"))
    table = pq.read_table(io.BytesIO(parquet.content))
    assert table.num_rows == 4 and "dev_pid" not in table.column_names
    assert people.get("/v2/export/identities", headers=h("tok-dir")).status_code == 404
    post(people, "tok-bia", "sq-a", [story_snap("US-9", feature='=HYPERLINK("x")')])
    evil = people.get("/v2/export/fact_story", headers=h("tok-dir")).text
    assert "'=HYPERLINK" in evil and ",=HYPERLINK" not in evil  # sem injeção de fórmula na planilha


# ---------------------------------------------------------------- HTML, sessão, SSO e segurança


def _session(client: Any, token: str) -> Any:
    resp = client.post(
        "/login",
        content=f"token={token}&next=/dash",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert resp.status_code == 303 and resp.headers["location"] == "/dash"
    return resp


def test_dashboard_html_session_and_security_headers(people: Any) -> None:
    seed(people)
    assert people.get("/dash/executive", follow_redirects=False).headers["location"].startswith("/login?next=")
    _session(people, "tok-lia")
    home = people.get("/dash", follow_redirects=False)
    assert home.headers["location"] == "/dash/executive"
    page = people.get("/dash/executive")
    html = page.text
    assert page.status_code == 200 and 'id="kpi-cost_month"' in html and "Referência: total da org" in html
    assert "<script" not in html and not re.search(r'(src|href)="https?://', html)  # RNF-23: sem CDN
    csp = page.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
    assert page.headers["x-frame-options"] == "DENY" and page.headers["cache-control"] == "no-store"
    for name in ("squad", "quality", "insights", "me"):
        assert people.get(f"/dash/{name}").status_code == 200, name
    assert people.get("/dash/routing").status_code == 403
    assert "sq-b" not in people.get("/dash/squad").text  # filtro só oferece o que o perfil vê
    drill = re.search(r'href="(/dash/drill\?[^"]+)"', html)
    assert drill and people.get(drill.group(1).replace("&amp;", "&")).status_code == 200
    people.get("/logout")
    assert people.get("/dash/executive", follow_redirects=False).status_code == 303
    bad = people.post("/login", content="token=tok-nada", headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert bad.status_code == 401


def test_sso_oidc_login_and_device_verification_page(people: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexo.central import auth

    seed(people)
    monkeypatch.setenv("NX_COLLECTOR_OIDC_ISSUER", "https://login.empresa.com/tenant")
    monkeypatch.setenv("NX_COLLECTOR_OIDC_CLIENT_ID", "nexo")
    monkeypatch.setenv("NX_COLLECTOR_OIDC_CLIENT_SECRET", "segredo")
    monkeypatch.setenv("NX_COLLECTOR_PUBLIC_URL", "https://nexo.empresa.com")
    seen: dict[str, Any] = {}

    def fake(method: str, url: str, form: dict[str, str] | None = None, bearer: str | None = None) -> dict[str, Any]:
        if url.endswith("/.well-known/openid-configuration"):
            return {
                "authorization_endpoint": "https://login.empresa.com/authorize",
                "token_endpoint": "https://login.empresa.com/token",
                "userinfo_endpoint": "https://login.empresa.com/userinfo",
            }
        if url.endswith("/token"):
            seen["form"] = form
            return {"access_token": "at-123"}
        assert bearer == "at-123"
        return {"preferred_username": "Lia@empresa.com"}

    monkeypatch.setattr(auth, "http_json", fake)
    start = people.get("/login?next=/dash/squad", follow_redirects=False)
    location = start.headers["location"]
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(location).query))
    assert location.startswith("https://login.empresa.com/authorize") and query["code_challenge_method"] == "S256"
    assert query["redirect_uri"] == "https://nexo.empresa.com/auth/callback"
    assert people.post("/login", content="token=tok-lia").status_code == 404  # token desligado com SSO
    back = people.get(f"/auth/callback?code=abc&state={query['state']}", follow_redirects=False)
    assert back.status_code == 303 and back.headers["location"] == "/dash/squad"
    assert "secure" in back.headers["set-cookie"].lower() and "httponly" in back.headers["set-cookie"].lower()
    assert seen["form"]["code_verifier"] and seen["form"]["client_secret"] == "segredo"
    sid = re.search(r"nexo_session=([^;]+)", back.headers["set-cookie"])
    assert sid is not None
    people.cookies.set("nexo_session", sid.group(1))  # cookie Secure: o cliente de teste fala http
    assert "<span>lia</span><small>perfil lead" in people.get("/dash/squad").text  # papel vindo do diretório
    assert people.get(f"/auth/callback?code=abc&state={query['state']}").status_code == 400  # estado de uso único
    code = people.post("/v2/device/code", json={"device_id": "dev-lia"}).json()
    page = people.get("/v2/device/verify")
    csrf = re.search(r'name=csrf value="([^"]+)"', page.text)
    assert csrf is not None
    denied = people.post("/v2/device/verify", content=f"csrf=errado&user_code={code['user_code']}")
    assert denied.status_code == 403
    ok = people.post("/v2/device/verify", content=f"csrf={csrf.group(1)}&user_code={code['user_code']}")
    assert ok.status_code == 200 and "dispositivo autorizado" in ok.text
    token = people.post("/v2/device/token", json={"device_code": code["device_code"]}).json()
    assert people.get("/v2/pages/squad", headers=h(token["access_token"])).json()["squads"] == ["sq-a"]


# ---------------------------------------------------------------- 11.4/11.5 insights e distribuição


def test_insights_flow_dashboard_and_cli(  # type: ignore[no-untyped-def]
    people: Any, project: Path, nexo: Runner, nexo_home: Path
) -> None:
    seed(people)
    for _ in range(4):  # I-02: tentativas demais em sq-a; I-14: fatura divergente
        post(people, "tok-bia", "sq-a", [story_snap(f"US-{uuid.uuid4().hex[:4]}", state="EmDev", attempts=3)])
    month = datetime.now(UTC).strftime("%Y-%m")
    people.post("/v2/invoices", json={"tool": "claude", "month": month, "amount_brl": 10.0}, headers=h("tok-fin"))
    result = people.post("/v2/admin/jobs/insights", headers=h("tok-admin")).json()["result"]
    assert result["opened"] >= 5 and not result["errors"]
    lia = people.get("/v2/insights", headers=h("tok-lia")).json()["items"]
    caio = people.get("/v2/insights", headers=h("tok-caio")).json()["items"]
    assert any and not any
    assert any  # org: todos veem
    target = next
    _session(people, "tok-lia")
    page = people.get("/dash/insights").text
    csrf = re.search(r'name=csrf value="([^"]+)"', page)
    assert csrf and target["title"] in page
    denied = people.post(f"/dash/insights/{target['id']}", content="csrf=x&state=reconhecido")
    assert denied.status_code == 403
    done = people.post(
        f"/dash/insights/{target['id']}",
        content=f"csrf={csrf.group(1)}&state=reconhecido&reason=",
        follow_redirects=False,
    )
    assert done.status_code == 303
    assert people.get("/v2/insights?state=reconhecido", headers=h("tok-lia")).json()["items"][0]["id"] == target["id"]
    # CLI do dev pelo token do dispositivo
    enable(nexo_home)
    login(nexo_home, people, "tok-sso-bia")
    code, out, err = nexo("--json", "insights", "list", "--state", "all")
    assert code == 0, err
    assert {i["detector"] for i in json.loads(out)} >= {"I-14"}


def test_weekly_monthly_and_systemic_distribution(people: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexo.central import distribution
    from nexo.central.common import db

    seed(people)
    for n in range(10):  # I-06 (sistêmico): rota ruim
        post(
            people,
            "tok-bia",
            "sq-a",
            [
                step("US-1", tool="claude", outcome="success" if n < 9 else "error"),
                step("US-1", tool="copilot", outcome="success" if n < 2 else "error"),
            ],
        )
    sent: list[tuple[str, dict[str, Any]]] = []

    def fake(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        sent.append((url, body))
        if "api.github" in url:
            assert headers["Authorization"] == "Bearer gh-token"
            return 201, {"html_url": "https://github.com/org/nexo-brain/issues/9"}
        return 200, {}

    monkeypatch.setattr(distribution, "transport", fake)
    monkeypatch.setenv(
        "NX_COLLECTOR_TEAMS_WEBHOOKS", json.dumps({"sq-a": "https://teams/a", "finops": "https://teams/f"})
    )
    monkeypatch.setenv("NX_COLLECTOR_GITHUB_TOKEN", "gh-token")
    monkeypatch.setenv("NX_COLLECTOR_BRAIN_REPO", "org/nexo-brain")
    people.post("/v2/admin/jobs/insights", headers=h("tok-admin"))
    weekly = people.post("/v2/admin/jobs/weekly", headers=h("tok-admin")).json()["result"]
    assert weekly == [{"target": "sq-a", "status": 200}]  # sq-b sem webhook: nada sai
    card = sent[0][1]
    assert distribution.validate_card(card) == [] and "bia" not in json.dumps(card)  # sem pessoas
    assert "squad sq-a" in json.dumps(card, ensure_ascii=False)
    month = datetime.now(UTC).strftime("%Y-%m")
    report = people.post(f"/v2/admin/jobs/monthly_finops?period={month}", headers=h("tok-admin")).json()["result"]
    assert report["delivery"]["status"] == 200 and all(not t["individual"] for t in report["tables"])
    issues = people.post("/v2/admin/jobs/issues", headers=h("tok-admin")).json()["result"]
    assert any(i["status"] == 201 for i in issues)
    issue = next(b for u, b in sent if "api.github" in u)
    assert issue["title"].startswith and "PR aprovado" in issue["body"]
    assert db().one["issue_url"].endswith("/9")
    again = people.post("/v2/admin/jobs/issues", headers=h("tok-admin")).json()["result"]
    assert not any(i["insight"] == issue for i in again) and len(again) < len(issues)  # uma issue por insight
    assert distribution.validate_card({"type": "message", "attachments": []}) == ["anexo adaptive card ausente"]
    assert people.post("/v2/admin/jobs/insights", headers=h("tok-bia")).status_code == 403


def test_scheduler_runs_each_job_once_per_period(people: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from zoneinfo import ZoneInfo

    from nexo.central import distribution
    from nexo.central.common import db

    monkeypatch.setattr(distribution, "transport", lambda *a: (200, {}))
    monday = datetime(2026, 9, 21, 10, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    first = distribution.tick(db(), monday.astimezone(UTC))
    assert {j["job"]: j["period"] for j in first} == {  # mensal atrasado roda na primeira chance (recuperação)
        "insights": "2026-09-21",
        "issues": "2026-09-21",
        "weekly": "2026-S39",
        "monthly_finops": "2026-08",
        "siem": "2026-09-21T10:00",
    }
    again = distribution.tick(db(), (monday + timedelta(hours=1)).astimezone(UTC))
    assert [j["job"] for j in again] == ["siem"]  # só o reenvio do SIEM (a cada 5 min); o resto já rodou
    early = datetime(2026, 9, 22, 5, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    assert distribution.due("insights", early) is None and distribution.due("weekly", early) == "2026-S39"
    assert distribution.due("monthly_finops", datetime(2026, 10, 1, 9, 30)) == "2026-09"
    assert distribution.due("monthly_finops", datetime(2026, 10, 1, 8, 0)) is None


def test_pages_stay_fast_with_a_year_of_steps(people: Any) -> None:
    """RNF-21 (amostra local): páginas com 12 meses de passos respondem em menos de 2 s."""
    from nexo.central.common import db

    seed(people)
    d = db()
    rows = []
    start = datetime.now(UTC) - timedelta(days=360)
    for i in range(20_000):
        at = start + timedelta(minutes=26 * i)
        rows.append(
            (
                uuid.uuid4().hex,
                "x",
                f"p{i % 7}",
                at.strftime("%Y-%m"),
                f"sq-{'ab'[i % 2]}",
                "python",
                f"s{i}",
                "r",
                "T-1",
                "US-1",
                "developer",
                "claude",
                "sonnet",
                at.isoformat(),
                1.0,
                "success",
                0.5,
                "1.0.0",
            )
        )
    with d.tx():
        for r in rows:
            d.execute(
                "INSERT INTO fact_step (event_id, dev_pid, project, month, squad, stack, step, run, target,"
                " story, role, tool, model, started_at, seconds, outcome, usage_brl, brain_version)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                r,
            )
    for page in ("executive", "finops", "routing", "brain"):
        t0 = time.perf_counter()
        resp = people.get(f"/v2/pages/{page}?month_from={start.strftime('%Y-%m')}", headers=h("tok-nx"))
        assert resp.status_code == 200 and time.perf_counter() - t0 < 2.0, page

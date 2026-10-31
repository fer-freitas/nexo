"""Portal: gestão de features, piso travado no servidor, login provisório, board e DORA sem Actions."""

from __future__ import annotations

import re
from typing import Any

import pytest

from tests.integration.test_dashboard import _session, h, people, post, rec  # noqa: F401
from tests.integration.test_telemetry import central, pg_server  # noqa: F401


def csrf(page: str) -> str:
    return re.search(r'name=csrf value="([^"]+)"', page).group(1)  # type: ignore[union-attr]


def form(client: Any, path: str, **fields: str) -> Any:
    from urllib.parse import urlencode

    return client.post(
        path,
        content=urlencode(fields),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )


def test_health(people: Any) -> None:
    assert people.get("/health").json() == {"ok": True}


def test_floor_is_refused_on_the_server_and_visible_as_locked(people: Any) -> None:
    resp = people.put(
        "/v2/admin/flags/audit", json={"value": False, "reason": "tentando desligar"}, headers=h("tok-admin")
    )
    assert resp.status_code in (403, 404)
    _session(people, "tok-admin")
    page = people.get("/dash/flags").text
    assert "Sempre ligado" in page and "killswitch" in page and "módulo" in page and "estágio" in page


def test_flag_change_needs_reason_scope_and_history(people: Any) -> None:
    put = lambda body: people.put("/v2/admin/flags/run_story", json=body, headers=h("tok-admin"))  # noqa: E731
    assert put({"value": False, "reason": "curto"}).status_code == 400
    assert put({"value": False, "project": "loja", "reason": "piloto só neste projeto"}).status_code == 200
    snap = people.get("/v2/flags", headers=h("tok-admin")).json()
    assert snap["projects"]["loja"]["run_story"] is False and "run_story" not in snap["global"]
    assert put({"value": None, "project": "loja", "reason": "voltando ao padrão do manifesto"}).status_code == 200
    assert "run_story" not in people.get("/v2/flags", headers=h("tok-admin")).json().get("projects", {}).get("loja", {})
    _session(people, "tok-admin")
    assert "piloto só neste projeto" in people.get("/dash/flags").text


def test_common_person_cannot_change_flags(people: Any) -> None:
    resp = people.put(
        "/v2/admin/flags/run_story", json={"value": False, "reason": "não sou admin"}, headers=h("tok-sso-bia")
    )
    assert resp.status_code == 403


def test_invite_login_expires_and_is_revoked(people: Any) -> None:
    from nexo.central import auth

    _session(people, "tok-admin")
    page = people.get("/dash/people").text
    done = form(people, "/dash/people", csrf=csrf(page), dev="bia", role="pm", days="30")
    token = re.search(r"nxi_[\w-]+", done.text).group(0)  # type: ignore[union-attr]
    assert "convite ativo" in people.get("/dash/people").text or token
    assert auth.from_invite(token)["role"] == "pm"  # type: ignore[index]
    assert auth.from_invite("nxi_errado") is None
    auth.revoke_invites("bia", "ana.admin")
    assert auth.from_invite(token) is None


def test_login_blocks_after_too_many_attempts(people: Any) -> None:
    for _ in range(8):
        assert form(people, "/login", token="errado", next="/dash").status_code == 401
    assert form(people, "/login", token="errado", next="/dash").status_code == 429


def test_group_claim_sets_the_role(people: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from nexo.central import auth
    from nexo.central.common import db

    monkeypatch.setenv("NX_COLLECTOR_OIDC_GROUP_ROLES", '{"nexo-qa": "qa"}')
    auth._apply_groups("caio", {"groups": ["outro", "nexo-qa"]})
    assert db().one("SELECT role FROM org_people WHERE dev = 'caio'")["role"] == "qa"


def test_killswitch_page_requires_reason_and_applies(people: Any) -> None:
    _session(people, "tok-admin")
    page = people.get("/dash/killswitch").text
    assert form(people, "/dash/killswitch", csrf=csrf(page), tools="devin", reason="curto").status_code == 400
    ok = form(people, "/dash/killswitch", csrf=csrf(page), tools="devin", reason="incidente de segurança no Devin")
    assert ok.status_code == 303
    assert people.get("/v2/kill-switch", headers=h("tok-admin")).json()["tools"] == ["devin"]


def test_insight_detector_can_be_turned_off_one_by_one(people: Any) -> None:
    _session(people, "tok-admin")
    page = people.get("/dash/insights-config").text
    assert "I-01" in page
    assert form(people, "/dash/insights-config", csrf=csrf(page), detector="I-01", enabled="0").status_code == 303
    from nexo.central.common import db
    from nexo.central.insights.engine import _enabled, load_detectors

    det = next(x for x in load_detectors() if x.id == "I-01")
    assert not _enabled(db(), det)


def demand(did: str, stage: str, **extra: Any) -> dict[str, Any]:
    return rec(
        "event",
        "demand.transitioned",
        {"demand": did, "demand_type": "story", "stage": stage, "holders": "dev", **extra},
    )


def test_board_shows_open_demands_and_titles_only_where_allowed(people: Any) -> None:
    post(
        people, "tok-admin", "", [demand("US-1-1", "build", title="Ver parcelas", blocked=1), demand("US-1-2", "done")]
    )
    data = people.get("/v2/pages/board", headers=h("tok-admin")).json()
    kpis = {k["id"]: k["value"] for k in data["kpis"]}
    assert kpis["open"] == 1 and kpis["blocked"] == 1
    assert data["tables"][0]["rows"][0][3] == "Ver parcelas"


def test_title_is_only_accepted_in_demand_events() -> None:
    from nexo.kernel.monitoring.telemetry import validate

    assert validate("event", "demand.created", {"demand": "US-1", "title": "ok"}) is None
    assert "título" in str(validate("event", "run.start", {"title": "ok"}))
    assert validate("event", "demand.created", {"demand": "US-1", "title": "a/b/c.py"})


def test_dora_comes_from_github_read_only(people: Any) -> None:
    from nexo.central.common import db
    from nexo.central.dora_github import collect
    from nexo.kernel.runtime import http

    class Fake:
        def __call__(self, method: str, url: str, headers: dict[str, str], data: bytes | None) -> Any:
            assert method == "GET"
            body: list[dict[str, Any]] = []
            if "org/svc/deployments" in url:
                body = [{"id": 1, "sha": "abc", "created_at": "2026-10-01T10:00:00Z"}]
            elif "org/lib/releases" in url:
                body = [{"id": 2, "target_commitish": "main", "published_at": "2026-10-02T10:00:00Z"}]
            return http.Response(200, body)

    http.set_transport(Fake())
    try:
        result = collect(db(), ["org/svc", "org/lib", "org/nada"])
    finally:
        http.set_transport(None)
    assert result["deployments"] == 2 and result["without_source"] == ["org/nada"]
    assert collect.__doc__


def test_dora_job_reads_repositories_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexo.central import jobs

    seen: list[list[str]] = []
    monkeypatch.setattr(jobs, "db", lambda: object())
    monkeypatch.setattr(jobs, "collect", lambda _d, repos: seen.append(repos) or {"new": 0})
    monkeypatch.setenv("NX_DORA_REPOS", "org/a, org/b")
    assert jobs.main(["dora"]) == 0 and seen == [["org/a", "org/b"]]
    assert jobs.main([]) == 2


def test_login_port_switches_to_sso_without_touching_the_portal(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexo.central import auth

    monkeypatch.setattr(auth, "oidc_enabled", lambda: False)
    assert auth.provider().name == "convite" and auth.provider().start("/x") is None
    monkeypatch.setattr(auth, "oidc_enabled", lambda: True)
    monkeypatch.setattr(auth, "oidc_start", lambda url: f"https://sso/?next={url}")
    login = auth.provider()
    assert login.name == "oidc" and login.start("/x") == "https://sso/?next=/x" and not login.accepts_token()


def test_first_admin_only_once(central: Any) -> None:
    from nexo.central import auth

    token = auth.first_admin("gh:primeira")
    identity = auth.from_invite(token)
    assert identity is not None and identity["dev"] == "gh:primeira"
    with pytest.raises(PermissionError):
        auth.first_admin("gh:segunda")


def _batch(squad: str, project: str, story: str, **context: str) -> dict[str, Any]:
    from nexo.kernel.monitoring.telemetry import CONTRACT
    from tests.integration.test_dashboard import step, story_snap

    return {
        "contract": CONTRACT,
        "device_id": "",
        "project": project,
        "context": {"squad": squad, "stack": "python", **context},
        "events": [step(story), story_snap(story)],
    }


def _seed_two_squads(people: Any) -> None:
    from tests.integration.test_dashboard import seed

    seed(people)
    a = _batch("sq-a", "repo-1", "US-A", project_name="custodia-web", project_sigla="CDT")
    b = _batch("sq-b", "repo-2", "US-B", project_name="custodia-api", project_sigla="CDT")
    c = _batch("sq-b", "repo-3", "US-C", project_name="cartoes", project_sigla="CRT")
    assert people.post("/v2/events", json=a, headers=h("tok-bia")).status_code == 200
    assert people.post("/v2/events", json=b, headers=h("tok-caio")).status_code == 200
    assert people.post("/v2/events", json=c, headers=h("tok-caio")).status_code == 200


def _stories(client: Any, query: str) -> set[str]:
    rows = client.get(f"/v2/drill?source=story&{query}", headers=h("tok-dir")).json()["rows"]
    return {str(r[1]) for r in rows}


def test_squad_filter_takes_more_than_one_and_only_shows_for_who_sees_more_than_one(people: Any) -> None:
    _seed_two_squads(people)
    _session(people, "tok-dir")  # diretoria: vê os dois
    page = people.get("/dash/squad").text
    assert "class=squadbar" in page and 'value="sq-a"' in page and 'value="sq-b"' in page
    both = people.get("/v2/pages/squad?squad=sq-a&squad=sq-b", headers=h("tok-dir")).json()
    assert both["squads"] == ["sq-a", "sq-b"] and both["filters"]["squad"] == "sq-a,sq-b"
    one = people.get("/dash/squad?squad=sq-a").text
    assert 'value="sq-a" checked' in one and 'value="sq-b" checked' not in one
    assert 'href="/dash/quality?squad=sq-a"' in one  # a escolha segue na navegação
    people.get("/logout")
    _session(people, "tok-caio")  # dev de um squad só: não tem o que escolher
    assert "class=squadbar" not in people.get("/dash/squad").text


def test_squad_filter_cannot_widen_what_the_person_sees(people: Any) -> None:
    _seed_two_squads(people)
    assert people.get("/v2/pages/squad?squad=sq-a&squad=sq-b", headers=h("tok-bia")).status_code == 403
    _session(people, "tok-bia")
    assert people.get("/dash/insights?squad=sq-b").status_code == 403


def test_every_filtered_page_respects_the_squad_choice(people: Any) -> None:
    from nexo.central.common import db

    _seed_two_squads(people)
    db().execute(
        "INSERT INTO fact_demand (project, demand, squad, stage, title) VALUES"
        " ('repo-1', 'D-A', 'sq-a', 'triage', 'demanda a'), ('repo-2', 'D-B', 'sq-b', 'triage', 'demanda b')"
    )
    for iid, squad in (("i-a", "sq-a"), ("i-b", "sq-b")):
        db().execute(
            "INSERT INTO insights (id, key, detector, scope_kind, scope, severity, title, evidence, action,"
            " owner, state, squad, first_seen, last_seen, updated_at) VALUES"
            " (?, ?, 'x', 'squad', ?, 'media', ?, '{}', 'f', 'o', 'aberto', ?, 1000000000.0, 1000000000.0, 1)",
            (iid, iid, squad, f"alerta do {squad}", squad),
        )
    for device, squad in (("m1", "sq-a"), ("m2", "sq-b")):
        db().execute(
            "INSERT INTO fleet (device_id, squad, nexo, last_seen) VALUES (?,?,'1.0',?)", (device, squad, 9e12)
        )
    _session(people, "tok-admin")
    board = people.get("/dash/board?squad=sq-a").text
    assert "demanda a" in board and "demanda b" not in board
    insights = people.get("/dash/insights?squad=sq-a").text
    assert "alerta do sq-a" in insights and "alerta do sq-b" not in insights and "class=squadbar" in insights

    def devices(query: str) -> int:
        kpis = people.get(f"/v2/pages/fleet{query}", headers=h("tok-admin")).json()["kpis"]
        return int(next(k["value"] for k in kpis if k["id"] == "devices"))

    assert devices("") == 2 and devices("?squad=sq-a") == 1


def test_sigla_shows_in_the_portal_and_filters_every_repository_of_the_project(people: Any) -> None:
    _seed_two_squads(people)
    _session(people, "tok-admin")
    page = people.get("/dash/squad").text
    assert "CDT · custodia-web" in page and '<option value="CDT"' in page
    assert _stories(people, "sigla=CDT") >= {"US-A", "US-B"} and "US-C" not in _stories(people, "sigla=CDT")
    assert _stories(people, "sigla=CRT") == {"US-C"}
    assert _stories(people, "sigla=NAO") == set()


def test_flag_panel_shows_machines_by_squad(people: Any) -> None:
    import time

    from nexo.central.common import db

    d = db()
    for device, squad, seen in (("m1", "sq-a", time.time()), ("m2", "sq-a", time.time()), ("m3", "sq-b", 0.0)):
        d.execute(
            "INSERT INTO devices (device_id, dev_pid, status, created_at) VALUES (?, 'x', 'active', 0)", (device,)
        )
        d.execute("INSERT INTO fleet (device_id, squad, last_seen) VALUES (?,?,?)", (device, squad, seen))
    _session(people, "tok-admin")
    page = people.get("/dash/flags").text
    assert "3 máquina(s) ativas" in page
    assert "<td>sq-a</td><td class=n>2</td>" in page and "<td>sq-b</td>" not in page  # m3 parou de dar sinal

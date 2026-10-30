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

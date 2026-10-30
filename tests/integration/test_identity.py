"""S-02: autoria e aprovação com identidade corporativa verificada."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from nexo.kernel.config.defaults import DEFAULTS
from nexo.kernel.runtime import http
from nexo.kernel.secrets import credentials
from tests.conftest import Runner
from tests.fakes.http_fake import FakeCloud
from tests.integration.test_story import OK_REVIEWS, QA, impl, run_json


@pytest.fixture
def cloud(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeCloud]:
    monkeypatch.setitem(DEFAULTS["identity"], "mode", "verified")
    fake = FakeCloud(github_users={"tok-dev": "dev-ana", "tok-lead": "lead-bia"})
    http.set_transport(fake)
    yield fake
    http.set_transport(None)


def human_gate_story(planned, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    nexo("config", "set", "gates.thresholds.code", "0.99", "--project")
    planned(scenario, [QA, impl("T-1-1"), impl("T-1-2", path="README.md"), *OK_REVIEWS])


def test_story_requires_verified_identity(planned, nexo: Runner, scenario, cloud: FakeCloud) -> None:  # type: ignore[no-untyped-def]
    human_gate_story(planned, nexo, scenario)
    code, _, err = nexo("run", "story", "US-1-1")
    assert code == 1 and "nexo login github" in err


def test_verified_author_cannot_approve_even_with_nexo_actor(
    planned,
    nexo: Runner,
    scenario,
    cloud: FakeCloud,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    human_gate_story(planned, nexo, scenario)
    credentials.set_secret("github", "tok-dev")
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "AguardandoHumano"
    monkeypatch.setenv("NX_ACTOR", "chefe")  # forjar o ator não muda a identidade verificada
    code, _, err = nexo("approve", "US-1-1")
    assert code == 5 and "SEC-52" in err
    credentials.set_secret("github", "tok-lead")
    code, rep = run_json(nexo, "approve", "US-1-1")
    assert code == 0 and rep["state"] == "Concluido"
    logs = json.loads(nexo("--json", "logs", "-n", "400")[1])
    decision = next(
        e
        for e in logs
        if e["type"] == "gate.decision"
        and e["payload"].get("gate") == "code"
        and e["payload"].get("decision") == "approved"
    )
    assert decision["payload"]["approver"] == "github:lead-bia" and decision["payload"]["approver_verified"] is True
    start = json.loads((Path.cwd() / ".nexo" / "runs").glob("*/story.json").__next__().read_text(encoding="utf-8"))
    assert start["author"] == "github:dev-ana"


def test_dev_cannot_downgrade_identity_or_allow_self_approval(
    planned,
    nexo: Runner,
    scenario,
    cloud: FakeCloud,  # type: ignore[no-untyped-def]
) -> None:
    human_gate_story(planned, nexo, scenario)
    credentials.set_secret("github", "tok-dev")
    nexo("config", "set", "identity.mode", "os", "--project")
    nexo("config", "set", "gates.allow_self_approval", "true")
    run_json(nexo, "run", "story", "US-1-1")
    code, _, err = nexo("approve", "US-1-1")
    assert code == 5 and "SEC-52" in err
    logs = json.loads(nexo("--json", "logs", "-n", "400")[1])
    assert any(e["type"] == "policy.violation" and "identity.mode" in str(e["payload"].get("reason")) for e in logs)


def test_org_can_allow_local_identity(planned, nexo: Runner, scenario, cloud: FakeCloud, nexo_home: Path) -> None:  # type: ignore[no-untyped-def]
    org = nexo_home / "brain" / "org"
    org.mkdir(parents=True, exist_ok=True)
    (org / "config.yaml").write_text("identity:\n  mode: os\n", encoding="utf-8")
    human_gate_story(planned, nexo, scenario)
    code, rep = run_json(nexo, "run", "story", "US-1-1")
    assert rep["state"] == "AguardandoHumano"  # sem token: a org liberou o modo local


def test_azure_identity_from_connection_data(nexo: Runner, cloud: FakeCloud, project: Path, nexo_home: Path) -> None:
    from nexo.kernel.context import AppContext
    from nexo.kernel.platform import identity

    nexo("config", "set", "tracker.kind", "azure", "--project")
    nexo("config", "set", "tracker.azure.org_url", cloud.org, "--project")
    credentials.set_secret("tracker", cloud.pat)
    ctx = AppContext(cwd=project, home=nexo_home)
    assert identity.current(ctx) == identity.Identity("azure:dev@empresa.com", verified=True)
    cloud.azure_users.clear()
    assert identity.current(ctx).id == "azure:dev@empresa.com"  # cache de 12 h, sem nova chamada


def test_doctor_shows_identity(nexo: Runner, cloud: FakeCloud) -> None:
    checks = {c["name"]: c for c in json.loads(nexo("--json", "doctor")[1])["checks"]}
    assert checks["identity"]["status"] == "warn" and "nexo login github" in checks["identity"]["detail"]
    credentials.set_secret("github", "tok-dev")
    checks = {c["name"]: c for c in json.loads(nexo("--json", "doctor")[1])["checks"]}
    assert checks["identity"]["status"] == "ok" and checks["identity"]["detail"] == "github:dev-ana"

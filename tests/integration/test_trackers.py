"""Integrações da F4 contra a nuvem simulada (formato das APIs oficiais)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from nexo.kernel.runtime import http
from nexo.kernel.runtime.resources import load_yaml
from nexo.kernel.tracker.mapping import Mapping
from nexo.modules.delivery.adapters.scm.github import GitHubScm
from nexo.modules.demands.adapters.base import TrackerItem
from nexo.modules.demands.adapters.github_issues import GitHubIssues
from tests.conftest import Runner
from tests.fakes.http_fake import FakeCloud


@pytest.fixture
def cloud() -> Iterator[FakeCloud]:
    fake = FakeCloud()
    http.set_transport(fake)
    yield fake
    http.set_transport(None)


def test_http_rejects_plain_http_and_raises_on_error(cloud: FakeCloud) -> None:
    http.set_transport(None)
    with pytest.raises(Exception, match="HTTPS"):
        http.request("GET", "http://example.com")
    http.set_transport(cloud)
    with pytest.raises(http.HttpError) as exc:
        http.request("GET", "https://api.github.com/unknown/route/x/y/z/w")
    assert exc.value.status == 404


def test_github_issues_tracker(cloud: FakeCloud) -> None:
    gh = GitHubIssues(
        "empresa/app", cloud.github_token, Mapping.from_mapping(load_yaml("workflows/mappings/github.yaml"))
    )
    ref = gh.create(TrackerItem("US-1", "story", "Estornar", "Pronto", "corpo", parent_ref="7"))
    issue = cloud.issues[int(ref.ref)]
    assert {x["name"] for x in issue["labels"]} == {"nexo", "type:story", "nexo-ready"} and "Parte de #7" in issue[
        "body"
    ]
    gh.transition(ref.ref, "Concluido", "entregue")
    assert issue["state"] == "closed" and issue["comments"] == ["Nexo: entregue"]
    assert gh.fetch([ref.ref])[0].state == "closed"
    closed = gh.create(TrackerItem("US-2", "story", "Cancelada", "Cancelado"))
    assert cloud.issues[int(closed.ref)]["state"] == "closed"
    assert gh.describe_process() is not None


def test_github_scm(cloud: FakeCloud, tmp_path: Path) -> None:
    scm = GitHubScm("empresa/app", cloud.github_token)
    pr = scm.open_pr("nexo/story/US-1", "AB#101 US-1: Estornar", "corpo")
    assert pr["base"] == "main" and pr["number"] == 1
    again = scm.open_pr("nexo/story/US-1", "AB#101 US-1: Estornar (v2)", "corpo 2")
    assert again["number"] == 1 and cloud.prs[0]["title"].endswith("(v2)")
    assert scm.checks("abc") == {"success": 2, "in_progress": 1}
    assert scm.protection("main") == {"protected": True, "required_reviews": 1, "required_checks": ["nexo-quality"]}
    assert scm.protection("dev") == {"protected": False}


def test_notifications(cloud: FakeCloud, nexo: Runner, memory_keyring, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    from nexo.modules.notify.adapters import channels as channels_notify

    memory_keyring.store[("nexo-cli", "teams")] = "https://hooks.teams.example/workflow/1"
    memory_keyring.store[("nexo-cli", "slack")] = "https://hooks.slack.example/T1"
    sent: list[dict[str, object]] = []
    monkeypatch.setattr(channels_notify, "email", lambda *a, **k: sent.append({"to": a[3], "title": a[4]}))
    for key, value in (
        ("notify.channels.teams", "true"),
        ("notify.channels.slack", "true"),
        ("notify.channels.email", "true"),
        ("notify.email.host", "smtp.empresa.com"),
        ("notify.email.to", "[time@empresa.com]"),
        ("notify.events.story", "[teams, slack, email]"),
    ):
        assert nexo("config", "set", key, value)[0] == 0, key
    from nexo.kernel.context import AppContext, Options
    from nexo.modules.notify.app import notify

    ctx = AppContext(options=Options())
    channels = notify(ctx, "story", "US-1: escalada", "3 tentativas esgotadas", ref="US-1", link="https://x/1")
    assert channels == ["teams", "slack", "email"]
    card = cloud.webhooks[0]["body"]["attachments"][0]["content"]  # type: ignore[index]
    assert card["type"] == "AdaptiveCard" and card["actions"][0]["url"] == "https://x/1"
    assert "*US-1: escalada*" in cloud.webhooks[1]["body"]["text"]  # type: ignore[index]
    assert sent == [{"to": ["time@empresa.com"], "title": "US-1: escalada"}]
    del memory_keyring.store[("nexo-cli", "teams")]
    cloud.fail_next = 1  # slack indisponível
    assert notify(ctx, "story", "t", "m") == ["email"]
    events = [e for e in ctx.events.recent(limit=5) if e.type == "notify"]
    assert "teams" in events[-1].payload["errors"] and "slack" in events[-1].payload["errors"]
    ctx.close()


def test_email_channel_uses_starttls(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexo.modules.notify.adapters import channels as channels_notify

    log: list[str] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            log.append(f"{host}:{port}")

        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def starttls(self) -> None:
            log.append("tls")

        def login(self, user: str, password: str) -> None:
            log.append(f"login {user}")

        def send_message(self, msg) -> None:  # type: ignore[no-untyped-def]
            log.append(msg["Subject"])

    monkeypatch.setattr(channels_notify.smtplib, "SMTP", FakeSMTP)
    channels_notify.email("smtp.x", 587, "nexo@x", ["a@x"], "Título", "Corpo", "user", "senha")
    assert log == ["smtp.x:587", "tls", "login user", "[Nexo] Título"]


def test_login_new_providers(nexo: Runner, monkeypatch: pytest.MonkeyPatch, memory_keyring) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("typer.prompt", lambda *a, **k: "segredo")
    providers = (
        "tracker",
        "github",
        "teams",
        "slack",
        "smtp",
        "anthropic",
        "openai",
        "azure_openai",
        "llm_gateway",
        "bedrock",
        "gemini_api",
        "gitlab",
        "bitbucket",
    )
    for provider in providers:
        assert nexo("login", provider)[0] == 0, provider
        assert memory_keyring.store[("nexo-cli", provider)] == "segredo"
    assert memory_keyring.store[("nexo-cli", "smtp")] == "segredo"
    events = json.loads(nexo("--json", "logs", "-n", "50")[1])
    assert "segredo" not in json.dumps(events)


# ---------------- ponta a ponta: planejamento → Azure Boards → história → PR no GitHub ----------------

from tests.conftest import git_cmd  # noqa: E402
from tests.integration.test_story import SPEC, backlog  # noqa: E402


@pytest.fixture
def tracked_project(
    project: Path,
    nexo: Runner,
    cloud: FakeCloud,
    memory_keyring,
    fast_recipe: Path,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> Path:
    memory_keyring.store[("nexo-cli", "github")] = cloud.github_token
    for key, value in (
        ("tracker.kind", "github"),
        ("tracker.github.repo", "empresa/app"),
        ("scm.provider", "github"),
        ("scm.repo", "empresa/app"),
        ("gates.thresholds.code", "0.9"),
    ):
        assert nexo("config", "set", key, value, "--project")[0] == 0
    remote = tmp_path / "remote.git"
    git_cmd(tmp_path, "init", "-q", "--bare", str(remote))
    git_cmd(project, "remote", "add", "origin", str(remote))
    return project


def test_tracker_outage_goes_to_queue(tracked_project: Path, nexo: Runner, scenario, cloud: FakeCloud) -> None:  # type: ignore[no-untyped-def]
    scenario([{"tool": "claude", "events": [SPEC]}])
    cloud.fail_next = 1
    assert nexo("plan", "versão")[0] == 0  # fluxo local segue mesmo com o tracker fora
    assert cloud.items == {}
    code, out, _ = nexo("--json", "tracker", "sync")
    data = json.loads(out)
    assert data["retried"] == 1 and data["pending"] == 0
    logs = json.loads(nexo("--json", "logs", "-n", "100")[1])
    assert any(e["type"] == "tracker.error" for e in logs)


def test_missing_credential_is_queued(tracked_project: Path, nexo: Runner, scenario, memory_keyring) -> None:  # type: ignore[no-untyped-def]
    del memory_keyring.store[("nexo-cli", "github")]
    scenario([{"tool": "claude", "events": [SPEC]}])
    assert nexo("plan", "versão")[0] == 0
    code, _, _ = nexo("tracker", "sync")
    assert code == 1


def test_file_exchange_roundtrip(project: Path, nexo: Runner, scenario, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    scenario(
        [
            {"tool": "claude", "events": [SPEC]},
            {"tool": "claude", "events": [backlog(["src/svc/version.py"], ["README.md"])]},
        ]
    )
    nexo("plan", "versão")
    nexo("plan", "approve", "F-1")
    code, out, _ = nexo("tracker", "export", "--out", str(tmp_path / "x"))
    csv_text = (tmp_path / "x" / "outbox.csv").read_text(encoding="utf-8")
    assert code == 0 and "User Story" in csv_text and "nexo-ready" in csv_text
    edited = csv_text.replace("US-1-1\n", "US-1-1\n").splitlines()
    header, rows = edited[0], edited[1:]
    rows = [r.replace(",New,", ",Active,") if ",US-1-1," in r else r for r in rows]
    (tmp_path / "in.csv").write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    code, out, _ = nexo("--json", "tracker", "import", str(tmp_path / "in.csv"))
    changes = json.loads(out)
    assert any(c["item"] == "US-1-1" and c["to"] == "EmDev" for c in changes)
    assert nexo("tracker", "sync")[0] == 1  # tracker local não sincroniza


def test_file_tracker_kind(project: Path, nexo: Runner, scenario) -> None:  # type: ignore[no-untyped-def]
    nexo("config", "set", "tracker.kind", "file", "--project")
    scenario([{"tool": "claude", "events": [SPEC]}])
    nexo("plan", "versão")
    assert "Versão" in (project / ".nexo/tracker/outbox.csv").read_text(encoding="utf-8")

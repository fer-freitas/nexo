"""console de ponta a ponta: a pessoa entra com `nexo console` e conduz tudo sem digitar `nexo`.

Pedido → plano → aprovação do plano → história (QA, dev, pipeline, revisão) → portão humano → aprovação, com
agentes simulados no formato real. Roda igual no Windows, no macOS e no Linux (CI).
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from tests.conftest import Runner, dump_yaml
from tests.integration.test_story import OK_REVIEWS, QA, SPEC, backlog, impl


def console(nexo: Runner, monkeypatch: pytest.MonkeyPatch, *lines: str) -> tuple[int, str]:
    monkeypatch.setattr("sys.stdin", io.StringIO("\n".join(lines) + "\n"))
    code, out, err = nexo("console")
    return code, out + err


def test_painel_drives_the_whole_flow_without_typing_nexo(
    project: Path,
    nexo: Runner,
    scenario,
    monkeypatch: pytest.MonkeyPatch,
    fast_recipe: Path,
    nexo_home: Path,
) -> None:  # type: ignore[no-untyped-def]
    nexo("config", "set", "gates.thresholds.code", "0.999", "--project")  # força o portão humano
    # responsável único (decisão da org, como nos testes reais): o autor pode aprovar o próprio portão
    dump_yaml(nexo_home / "brain" / "org" / "config.yaml", {"gates": {"allow_self_approval": True}})
    scenario(
        [
            {"tool": "claude", "events": [SPEC]},
            {"tool": "claude", "events": [backlog(["src/svc/version.py"], ["README.md"])]},
            QA,
            impl("T-1-1"),
            impl("T-1-2", path="README.md"),
            *OK_REVIEWS,
        ]
    )
    code, out = console(
        nexo,
        monkeypatch,
        "?",
        "quero planejar a consulta de versão do serviço",  # frase comum: vira `plan` sem IA
        "",  # Enter confirma
        "aprova o plano da F-1",
        "",
        "?",
        "roda a US-1-1",
        "",
        "",  # parou no portão: Enter deixa a decisão para depois
        "como está a US-1-1?",
        "aprova ela",  # foco da conversa: US-1-1
        "",
        "sair",
    )
    assert code == 0, out
    assert "Para agora" in out
    assert "→ plan 'consulta de versão do serviço'" in out
    assert "→ plan approve F-1" in out
    assert "→ run story US-1-1" in out
    assert "→ approve US-1-1" in out
    status = json.loads(nexo("--json", "status", "US-1-1")[1])
    assert status["state"] == "Concluido", out[-2000:]
    events = [e["type"] for e in json.loads(nexo("--json", "logs", "-n", "500")[1])]
    starts = [e for e in json.loads(nexo("--json", "logs", "-n", "500")[1]) if e["type"] == "command.start"]
    argvs = [e["payload"]["argv"] for e in starts]
    assert ["console"] in argvs and ["approve", "US-1-1"] in argvs  # cada ação do console é auditada como na CLI
    assert "gate.decision" in events


def test_painel_cancels_by_default_and_never_runs_unknown_text(
    project: Path, nexo: Runner, scenario, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    scenario([])
    code, out = console(nexo, monkeypatch, "roda a US-9-9", "talvez", "n", "7", "sair")
    assert code == 0
    assert "cancelado" in out and "não há opção 7" in out
    starts = [
        e["payload"]["argv"] for e in json.loads(nexo("--json", "logs", "-n", "100")[1]) if e["type"] == "command.start"
    ]
    assert ["run", "story", "US-9-9"] not in starts


def test_painel_outside_a_project_offers_init(tmp_path: Path, nexo: Runner, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "novo"
    folder.mkdir()
    monkeypatch.chdir(folder)
    code, out = console(nexo, monkeypatch, "1", "sair")
    assert code == 0 and "preparar esta pasta" in out
    assert (folder / ".nexo" / "nexo.yaml").is_file()


def test_assistant_only_for_what_the_panel_does_not_understand(
    project: Path, nexo: Runner, scenario, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    answer = {"reply": "Para ver o que pesa no custo, use o relatório por história.", "command": "cost --story US-1-1"}
    scenario([{"tool": "claude", "role": "assistant", "events": [{"done": {"structured": answer}}]}])
    code, out = console(nexo, monkeypatch, "o que mais pesa no meu orçamento esse mês?", "sair")
    assert code == 0, out
    assert "relatório por história" in out and "→ cost --story US-1-1" in out  # consulta: roda sem confirmar


def test_assistant_command_outside_the_catalog_is_refused(
    project: Path, nexo: Runner, scenario, monkeypatch: pytest.MonkeyPatch
) -> None:  # type: ignore[no-untyped-def]
    bad = {"reply": "vou apagar", "command": "rm -rf /"}
    scenario([{"tool": "claude", "role": "assistant", "events": [{"done": {"structured": bad}}]}] * 3)
    code, out = console(nexo, monkeypatch, "limpa tudo do projeto pra mim", "sair")
    assert code == 0 and "IA não está disponível" in out and "→ rm" not in out


# ---------------- GitHub (PR e CI) com as demandas só nos arquivos do projeto ----------------

from collections.abc import Iterator  # noqa: E402

from nexo.kernel.runtime import http
from tests.conftest import git_cmd  # noqa: E402
from tests.fakes.http_fake import FakeCloud  # noqa: E402


@pytest.fixture
def cloud() -> Iterator[FakeCloud]:
    fake = FakeCloud()
    http.set_transport(fake)
    yield fake
    http.set_transport(None)


def test_painel_with_local_demands_and_github_pr(
    project: Path,
    nexo: Runner,
    scenario,
    monkeypatch: pytest.MonkeyPatch,
    fast_recipe: Path,
    nexo_home: Path,
    cloud: FakeCloud,
    memory_keyring,
    tmp_path: Path,
) -> None:  # type: ignore[no-untyped-def]
    memory_keyring.store[("nexo-cli", "github")] = cloud.github_token
    for key, value in (
        ("scm.provider", "github"),
        ("scm.repo", "empresa/app"),
        ("gates.thresholds.code", "0.999"),
    ):
        assert nexo("config", "set", key, value, "--project")[0] == 0
    dump_yaml(nexo_home / "brain" / "org" / "config.yaml", {"gates": {"allow_self_approval": True}})
    remote = tmp_path / "remote.git"
    git_cmd(tmp_path, "init", "-q", "--bare", str(remote))
    git_cmd(project, "remote", "add", "origin", str(remote))
    scenario(
        [
            {"tool": "claude", "events": [SPEC]},
            {"tool": "claude", "events": [backlog(["src/svc/version.py"], ["README.md"])]},
            QA,
            impl("T-1-1"),
            impl("T-1-2", path="README.md"),
            *OK_REVIEWS,
        ]
    )
    code, out = console(
        nexo,
        monkeypatch,
        'plan "consulta de versão do serviço"',
        "",
        "aprova o plano da F-1",  # (o 1 agora é a análise do tech lead)
        "",
        "roda a US-1-1",
        "",
        "a",  # parou no portão: decide ali mesmo, por tecla
        "como está o PR?",
        "",
        "sair",
    )
    assert code == 0, out[-2000:]
    assert json.loads(nexo("--json", "status", "US-1-1")[1])["state"] == "Concluido", out[-2000:]
    assert cloud.prs and cloud.prs[0]["head"] == "nexo/story/US-1-1", out[-2000:]
    assert git_cmd(tmp_path, "--git-dir", str(remote), "branch", "--list", "nexo/story/US-1-1")
    assert "→ pr sync" in out
    # demandas locais: nada foi para tracker externo, tudo está nos arquivos do projeto
    assert json.loads(nexo("--json", "config", "get", "tracker.kind")[1]).get("value") in (None, "local")
    folder = next(project.glob(".nexo/specs/F-1-*"))
    assert (folder / "spec.yaml").is_file() and (folder / "backlog.yaml").is_file()


def test_closed_output_is_not_an_error(tmp_path: Path) -> None:
    """`nexo ... | head` / `grep -q`: quem lê fecha a saída antes; achado no CI (Windows: OSError; Rich: código 1)."""
    import os
    import subprocess
    import sys

    env = {**os.environ, "NX_HOME": str(tmp_path / "home"), "PYTHONUTF8": "1"}
    for args, feed in ((["help"], ""), (["console"], "ajuda\najuda\najuda\nsair\n")):
        proc = subprocess.Popen(
            [sys.executable, "-m", "nexo", *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=tmp_path,
            env=env,
        )
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(feed.encode("utf-8"))
        proc.stdin.close()
        proc.stdout.readline()
        proc.stdout.close()  # o leitor desiste depois da primeira linha
        assert proc.wait(timeout=60) == 0, proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""

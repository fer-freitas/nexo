"""MCP: versão fixa, aprovação pelo destino e estado só com evidência real."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from nexo.kernel.context import AppContext, Options
from nexo.kernel.workspace.specs import project_settings
from nexo.modules.mcp.app import catalog
from nexo.modules.mcp.domain import rules
from tests.conftest import Runner, dump_yaml

SERVER = """
import json, sys
for line in sys.stdin:
    m = json.loads(line)
    if "id" not in m:
        continue
    if m["method"] == "initialize":
        r = {"protocolVersion": "2025-03-26", "capabilities": {}, "serverInfo": {"name": "t", "version": "1"}}
    elif m["method"] == "tools/list":
        r = {"tools": [{"name": "ler", "inputSchema": {"type": "object"}}]}
    else:
        r = {"content": [{"type": "text", "text": "ok"}]}
    print(json.dumps({"jsonrpc": "2.0", "id": m["id"], "result": r}), flush=True)
"""


@pytest.mark.parametrize(
    "entry,ok",
    [
        ({"transport": "stdio", "command": "npx", "args": ["-y", "pacote"]}, False),
        ({"transport": "stdio", "command": "npx", "args": ["-y", "pacote@latest"]}, False),
        ({"transport": "stdio", "command": "npx", "args": ["-y", "@org/pacote@1.2.3"]}, True),
        ({"transport": "stdio", "command": "docker", "args": ["run", "imagem:1"]}, False),
        ({"transport": "stdio", "command": "docker", "args": ["run", "imagem@sha256:" + "a" * 64]}, True),
        ({"transport": "http", "url": "https://x"}, True),
    ],
)
def test_version_must_be_pinned(entry: dict, ok: bool) -> None:
    assert (rules.pin_problem(entry) == "") is ok


def test_every_stdio_runner_in_catalog_needs_a_pin() -> None:
    for server, entry in catalog.catalog().items():
        if entry.get("command") in ("npx", "docker"):
            assert rules.pin_problem(entry), server  # sem a variável, ainda não está fixado
            values = {"version": "1.0.0", "digest": "sha256:" + "a" * 64, "organization": "o"}
            assert rules.pin_problem(entry, values) == "", server


def test_identity_is_the_destination_not_the_name() -> None:
    a = rules.identity({"transport": "http", "url": "https://a"})
    assert a.key == "http:https://a"
    b = rules.identity({"transport": "stdio", "command": "npx", "args": ["x@{version}"]}, {"version": "2.0.0"})
    assert b.key == "stdio:npx x@2.0.0"


def fake_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = tmp_path / "srv.py"
    script.write_text(SERVER, encoding="utf-8")
    entry = {
        "transport": "stdio",
        "command": sys.executable,
        "args": ["-I", str(script)],
        "vendor": "v",
        "product": "Teste",
    }
    monkeypatch.setattr(catalog, "catalog", lambda: {"teste": entry})


def test_state_needs_evidence_and_tool_call(
    tmp_path: Path, project: Path, nexo: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_catalog(tmp_path, monkeypatch)
    out = json.loads(nexo("--json", "mcp", "add", "teste")[1])
    assert out["state"] == "configured" and out["approve"]["match"].startswith("stdio:")
    ctx = AppContext(options=Options())
    assert catalog.state_of(ctx, "teste") == "configured"
    assert catalog.probe(ctx, "teste")["state"] == "connected"
    assert catalog.probe(ctx, "teste", "ler")["state"] == "used"
    with pytest.raises(Exception, match="não oferece"):
        catalog.probe(ctx, "teste", "apagar")


def test_approval_is_for_the_exact_destination(
    tmp_path: Path, project: Path, nexo: Runner, nexo_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_catalog(tmp_path, monkeypatch)
    dump_yaml(
        nexo_home / "brain" / "org" / "mcp-allowlist.yaml", {"servers": [{"id": "teste", "match": "stdio:outro 1.0"}]}
    )
    ctx = AppContext(options=Options())
    assert "destino diferente" in catalog.identity_problem(ctx, "teste", catalog.catalog()["teste"], {})


def test_approve_requires_tech_lead_and_only_prepares_the_pr(
    tmp_path: Path, nexo: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    server = next(k for k, v in catalog.catalog().items() if v.get("transport") == "http")
    clone = tmp_path / "brain"
    clone.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=clone, check=True)
    (clone / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-qm", "i"], cwd=clone, check=True)
    project = tmp_path / "p"
    project.mkdir()
    monkeypatch.chdir(project)
    assert nexo("init")[0] == 0
    team_file = project_settings(project).team_dir() / "team.yaml"
    dump_yaml(team_file, {"name": "t", "mode": "team", "members": [{"id": "outra", "roles": ["tl"]}]})
    code, _, err = nexo("mcp", "approve", server, "--brain", str(clone))
    assert code != 0 and "tech lead" in err
    team_file.unlink()
    code, out, err = nexo("--json", "mcp", "approve", server, "--brain", str(clone))
    assert code == 0, err + out
    assert json.loads(out)["branch"] == f"nexo/mcp/{server}"
    assert server in (clone / "org" / "mcp-allowlist.yaml").read_text(encoding="utf-8")
    context = AppContext(options=Options())
    assert server not in catalog.allowlist(context), "sem PR e assinatura, a aprovação não vale"

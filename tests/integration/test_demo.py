"""A inicialização rápida funciona de verdade: o roteiro que ela sugere é executado aqui, comando a comando."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from nexo.modules.demo.app import cli_demo, site_demo


def test_walkthrough_runs_as_printed_without_tokens(tmp_path: Path) -> None:
    data = cli_demo.prepare(tmp_path / "d")
    env = cli_demo.env_for(tmp_path / "d")
    project = Path(data["project"])
    results = {}
    for step in data["walkthrough"]:
        words = shlex.split(step["command"])
        done = subprocess.run(
            [sys.executable, "-m", "nexo", *words[1:]],
            cwd=project,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        results[step["command"]] = done.stdout + done.stderr
        assert done.returncode == 0, f"{step['command']}:\n{done.stdout}\n{done.stderr}"
    assert "US-1-1" in results["nexo board"]
    assert "aprovado sozinho" in results["nexo tl gate US-1-1"] or "decisão humana" in results["nexo tl gate US-1-1"]


def test_demo_never_touches_the_real_tool_settings(tmp_path: Path) -> None:
    """A demonstração promete não mexer em nada: a guarda real do Claude Code não pode ser instalada por ela."""
    home = tmp_path / "pessoa"
    (home / ".claude").mkdir(parents=True)
    settings = home / ".claude" / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    env = {**cli_demo.env_for(tmp_path / "d"), "HOME": str(home), "USERPROFILE": str(home)}
    env.pop("CLAUDE_CONFIG_DIR", None)
    data = cli_demo.prepare(tmp_path / "d")
    for args in (["pm"], ["console"], ["board"]):
        subprocess.run(
            [sys.executable, "-m", "nexo", *args],
            cwd=data["project"],
            env=env,
            input="sair\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    assert settings.read_text(encoding="utf-8") == "{}"
    assert not list(home.rglob("*.nexo-bak"))


def test_refuses_a_folder_with_files(tmp_path: Path) -> None:
    (tmp_path / "x").write_text("x", encoding="utf-8")
    with pytest.raises(Exception, match="não está vazia"):
        cli_demo.prepare(tmp_path)


def test_seed_fills_the_portal_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    tokens = site_demo.people()
    devs = {f"tok-{dev}": {"role": "dev", "dev": dev} for dev, _p in site_demo.SQUADS.values()}
    monkeypatch.setenv("NX_COLLECTOR_DB", str(tmp_path / "demo.db"))
    monkeypatch.setenv("NX_COLLECTOR_TOKENS", json.dumps({**tokens, **devs}))
    monkeypatch.setenv("NX_COLLECTOR_PSEUDO_KEY", "chave")
    from nexo.central import app as collector_app

    collector_app._dbs.clear()
    client = TestClient(collector_app.app)

    def post(method: str, path: str, body: Any, token: str) -> None:
        answer = client.request(method, path, json=body, headers={"Authorization": f"Bearer {token}"})
        assert answer.status_code == 200, answer.text

    seed_tokens = {v["dev"]: k for k, v in devs.items()} | {v["role"]: k for k, v in tokens.items()}
    site_demo.seed(post, seed_tokens)
    admin = next(k for k, v in tokens.items() if v["role"] == "admin")
    answer = client.post(
        "/login",
        content=f"token={admin}&next=/dash",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=True,
    )
    assert answer.status_code == 200
    for page in ("executive", "finops", "squad", "quality", "flags", "people", "insights"):
        got = client.get(f"/dash/{page}")
        assert got.status_code == 200, page
    assert "R$" in client.get("/dash/finops").text
    assert "sq-pagamentos" in client.get("/dash/squad").text
    assert "US-101-1-PAG" in client.get("/dash/board").text, "o board da demonstração não pode vir vazio"
    assert client.get("/dash/executive").status_code == 200
    collector_app._dbs.clear()


def test_site_really_starts_and_only_listens_locally(tmp_path: Path) -> None:
    pytest.importorskip("uvicorn")
    data = site_demo.start(0, tmp_path / "site")
    server = data.pop("process")
    try:
        assert data["url"].startswith("http://127.0.0.1:") and "admin" in data["logins"]
        import urllib.request

        with urllib.request.urlopen(data["url"] + "/health", timeout=5) as answer:  # noqa: S310
            assert answer.status == 200
    finally:
        server.terminate()
        server.wait(timeout=10)

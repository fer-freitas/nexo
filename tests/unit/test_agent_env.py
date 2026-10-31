from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from nexo.kernel.models.agent import AgentEvent, AgentOutcome, AgentRequest
from nexo.kernel.runtime.resources import load_yaml
from nexo.kernel.security.environment import filter_env
from nexo.kernel.security.policy import Policy
from nexo.modules.execution.adapters.agents.base import SubprocessSession

ENV = {
    "PATH": "/usr/bin",
    "HOME": "/home/dev",
    "LC_ALL": "pt_BR.UTF-8",
    "HTTPS_PROXY": "http://proxy:8080",
    "NX_HOME": "/home/dev/.nexo",
    "NX_ACTOR": "chefe",
    "NX_COLLECTOR_TOKEN": "t0k",
    "AWS_SECRET_ACCESS_KEY": "aws",
    "OUTRO_EXT_PAT": "pat",
    "GITHUB_TOKEN": "ghp",
    "DB_PASSWORD": "senha",
    "ANTHROPIC_API_KEY": "sk-ant",
    "RANDOM_APP_SETTING": "x",
}


def test_only_allowlisted_names_pass() -> None:
    kept, removed = filter_env(ENV)
    assert set(kept) == {"PATH", "HOME", "LC_ALL", "HTTPS_PROXY", "NX_HOME"}
    assert "AWS_SECRET_ACCESS_KEY" in removed and "RANDOM_APP_SETTING" in removed
    assert "NX_ACTOR" in removed and "NX_COLLECTOR_TOKEN" in removed  # identidade e segredo do próprio Nexo


def test_explicit_allow_is_the_only_way_for_secrets() -> None:
    kept, _ = filter_env(ENV, ("GITHUB_TOKEN", "ANTHROPIC_*"))
    assert kept["GITHUB_TOKEN"] == "ghp" and kept["ANTHROPIC_API_KEY"] == "sk-ant"
    assert "DB_PASSWORD" not in kept
    kept, _ = filter_env(ENV, ("NX_*",))
    assert "NX_ACTOR" not in kept  # nunca, nem por padrão amplo


def test_windows_names_are_case_insensitive() -> None:
    kept, _ = filter_env({"Path": "C:\\bin", "SystemRoot": "C:\\Windows", "UserProfile": "C:\\Users\\dev"})
    assert set(kept) == {"Path", "SystemRoot", "UserProfile"}


def test_policy_gives_each_tool_only_its_own_credential() -> None:
    policy = Policy.from_mapping(load_yaml("policy.yaml")).with_locked(frozenset())
    assert "GITHUB_TOKEN" in policy.agent_env_allow("copilot")
    assert "GITHUB_TOKEN" not in policy.agent_env_allow("claude")
    assert policy.agent_env_allow("desconhecida") == ()


class _Lines:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def feed(self, line: str) -> list[AgentEvent]:
        self.lines.append(line)
        return []

    def finish(self, returncode: int, stderr: str) -> AgentOutcome:
        return AgentOutcome("success")


def test_agent_process_receives_filtered_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp")
    monkeypatch.setenv("RANDOM_APP_SETTING", "x")
    parser = _Lines()
    req = AgentRequest(
        "developer", "", str(tmp_path), "m", env={"NX_RUN_ID": "r1"}, env_allow=("GITHUB_TOKEN",), timeout_s=60
    )
    cmd = [sys.executable, "-c", "import os, json; print(json.dumps(sorted(os.environ)))"]
    list(SubprocessSession(cmd, req, parser).events())
    names = set(json.loads(parser.lines[-1]))
    assert {"GITHUB_TOKEN", "NX_RUN_ID"} <= names
    assert not names & {"AWS_SECRET_ACCESS_KEY", "RANDOM_APP_SETTING"}

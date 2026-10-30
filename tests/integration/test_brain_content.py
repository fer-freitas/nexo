"""conteúdo vindo do cérebro — prompts com contrato, piso de segurança, hooks/tools da org, canário,
compatibilidade, migração da baseline, diff, âncora da org e baseline do build."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexo.kernel.runtime.resources import read_text
from tests.conftest import DEFAULT_TASK, Runner, dump_yaml, edit, git_cmd

MARK = "REGRA-DA-ORG-7731"


def brain_repo(tmp_path: Path, nexo: Runner, files: dict[str, str], name: str = "nexo-brain") -> tuple[Path, str, Path]:
    repo = tmp_path / name
    nexo("brain", "init", str(repo))
    key = tmp_path / f"{name}.key"
    pub = nexo("brain", "keygen", str(key))[1].strip().splitlines()[-1].split(": ")[-1]
    for rel, content in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(content, encoding="utf-8")
    resign(nexo, repo, key, "2026.10.01-1")
    return repo, pub, key


def resign(nexo: Runner, repo: Path, key: Path, version: str) -> None:
    assert nexo("brain", "sign", str(repo), "--key", str(key), "--version", version)[0] == 0
    git_cmd(repo, "add", "-A")
    git_cmd(repo, "commit", "-qm", version)


def use(nexo: Runner, repo: Path, pub: str) -> tuple[int, str, str]:
    nexo("config", "set", "brain.auto_pull", "false")
    nexo("config", "set", "brain.repo", str(repo))
    nexo("config", "set", "brain.trusted_keys", f"[{pub}]")
    return nexo("brain", "pull")


def events(nexo: Runner) -> list[dict]:  # type: ignore[type-arg]
    return json.loads(nexo("--json", "logs", "-n", "400")[1])


def dev_prompt_with_mark() -> str:
    return read_text("prompts/developer.md") + f"\n\nRegra obrigatória da org: {MARK}.\n"


def test_prompt_from_brain_reaches_agent_without_release(
    project: Path,
    nexo: Runner,
    scenario,  # type: ignore[no-untyped-def]
    tmp_path: Path,
) -> None:
    dump_yaml(project / ".nexo/tasks/T-2.yaml", DEFAULT_TASK)
    scenario(
        [
            {"tool": "claude", "task": t, "role": "developer", "expect_prompt": MARK, "events": [edit()]}
            for t in ("T-1", "T-2")
        ]
    )
    assert json.loads(nexo("--json", "run", "task", "T-1")[1])["state"] != "Concluido"  # baseline não tem a regra
    repo, pub, _ = brain_repo(tmp_path, nexo, {"prompts/developer.md": dev_prompt_with_mark()})
    assert use(nexo, repo, pub)[0] == 0
    assert json.loads(nexo("--json", "run", "task", "T-2")[1])["state"] == "Concluido"
    used = [e["payload"] for e in events(nexo) if e["type"] == "prompt.used" and e["payload"]["role"] == "developer"]
    assert used[-1]["source"] == "brain@2026.10.01-1" and used[0]["source"] == "embutido"
    assert used[-1]["sha256"] != used[0]["sha256"]


def test_prompt_breaking_contract_is_refused(workdir: Path, nexo: Runner, tmp_path: Path, nexo_home: Path) -> None:
    good = {"prompts/developer.md": dev_prompt_with_mark()}
    repo, pub, key = brain_repo(tmp_path, nexo, good)
    assert use(nexo, repo, pub)[0] == 0
    broken = read_text("prompts/developer.md").replace("{acceptance}", "(critérios)") + "{segredo}"
    (repo / "prompts/developer.md").write_text(broken, encoding="utf-8")
    resign(nexo, repo, key, "2026.10.01-2")
    code, _, err = nexo("brain", "pull")
    assert code == 5 and "contrato quebrado" in err and "acceptance" in err and "segredo" in err
    assert MARK in (nexo_home / "brain/prompts/developer.md").read_text(encoding="utf-8")  # cache anterior mantido
    # cache adulterado localmente: a execução ignora o prompt e usa a baseline
    (nexo_home / "brain/prompts/developer.md").write_text(broken, encoding="utf-8")
    from nexo.kernel.context import AppContext
    from nexo.modules.knowledge.app import content as knowledge_content

    ctx = AppContext(cwd=workdir, home=nexo_home)
    assert knowledge_content.prompt(ctx, "developer") == read_text("prompts/developer.md")
    assert any(e["type"] == "brain.content_rejected" for e in events(nexo))


@pytest.mark.parametrize(
    ("compat", "message"),
    [("schema_version: 2\n", "schema_version 2"), ("schema_version: 1\nmin_nexo: 99.0.0\n", "nexo update")],
)
def test_incompatible_brain_keeps_previous_cache(
    workdir: Path, nexo: Runner, tmp_path: Path, compat: str, message: str
) -> None:
    repo, pub, key = brain_repo(tmp_path, nexo, {"brain.yaml": "schema_version: 1\n"})
    assert use(nexo, repo, pub)[0] == 0
    (repo / "brain.yaml").write_text(compat, encoding="utf-8")
    resign(nexo, repo, key, "2026.10.01-9")
    code, _, err = nexo("brain", "pull")
    assert code == 5 and message in err
    assert json.loads(nexo("--json", "brain", "status")[1])["version"] == "2026.10.01-1"


def test_org_policy_cannot_loosen_security_floor(workdir: Path, nexo: Runner, tmp_path: Path, nexo_home: Path) -> None:
    from ruamel.yaml import YAML

    floor = YAML(typ="safe").load(read_text("policy.yaml"))
    org = dict(floor)
    org["shell_deny"] = floor["shell_deny"][1:]  # tenta remover uma negação
    org["shell_allow"] = [*floor["shell_allow"], "curl"]  # tenta liberar comando
    org["forbidden_paths"] = [*floor["forbidden_paths"], "legacy/secrets/**"]  # restringe: aceito
    org["network"] = {"allowed_hosts": ["exfil.example.com"]}
    org["agent_env"] = {"allow": ["JAVA_OPTS", "AWS_SECRET_ACCESS_KEY"], "tools": floor["agent_env"]["tools"]}
    import io

    buf = io.StringIO()
    YAML().dump(org, buf)
    repo, pub, _ = brain_repo(tmp_path, nexo, {"org/security/policy.yaml": buf.getvalue()})
    assert use(nexo, repo, pub)[0] == 0
    from nexo.kernel.context import AppContext
    from nexo.modules.execution.app import hooks

    ctx = AppContext(cwd=workdir, home=nexo_home)
    policy = hooks.load_policy(ctx)
    hooks.load_policy(ctx)  # segunda carga não repete o relato
    assert floor["shell_deny"][0]["pattern"] in [p.pattern for p, _ in policy.shell_deny]
    assert "curl" not in policy.shell_allow and policy.allowed_hosts == ()
    assert "legacy/secrets/**" in policy.forbidden_paths
    assert "JAVA_OPTS" in policy.env_allow and "AWS_SECRET_ACCESS_KEY" not in policy.env_allow
    violations = [e for e in events(nexo) if e["payload"].get("rule") == "SEC-76"]
    assert len(violations) == 4  # deny removido, curl, host, variável sensível — uma vez só
    assert any(e["type"] == "brain.issue_skipped" for e in events(nexo))  # issue (sem repo de issues configurado)


def test_org_hooks_and_tools(project: Path, nexo: Runner, tmp_path: Path, nexo_home: Path) -> None:
    repo, pub, key = brain_repo(
        tmp_path,
        nexo,
        {
            "org/hooks.yaml": "disable: [story_notify]\nhandoff: false\n",
            "org/tools.yaml": "roles:\n  reviewer: [get_task_context]\nexternal_mcp: [playwright]\n",
        },
    )
    assert use(nexo, repo, pub)[0] == 0
    from nexo.kernel.context import AppContext
    from nexo.modules.execution.app import hooks
    from nexo.modules.execution.app.run import Orchestrator, RunOptions
    from nexo.modules.knowledge.app import content as knowledge_content

    ctx = AppContext(cwd=project, home=nexo_home)
    assert not knowledge_content.hook_enabled(ctx, "story_notify") and knowledge_content.hook_enabled(ctx, "pre_send")
    assert Orchestrator(ctx, RunOptions()).max_handoffs == 0
    assert knowledge_content.tools_for_role(ctx, "reviewer") == {"get_task_context"}
    assert knowledge_content.tools_for_role(ctx, "developer") is None
    assert "playwright" in hooks.load_policy(ctx).allowed_mcp
    checks = {c["name"]: c for c in json.loads(nexo("--json", "doctor")[1])["checks"]}
    assert (
        "desligados pela org: story_notify" in checks["hooks"]["detail"] and "playwright" in checks["hooks"]["detail"]
    )
    # hook de segurança não desliga; tool desconhecida não entra
    (repo / "org/hooks.yaml").write_text("disable: [pre_send]\n", encoding="utf-8")
    (repo / "org/tools.yaml").write_text("roles:\n  reviewer: [shell_exec]\n", encoding="utf-8")
    resign(nexo, repo, key, "2026.10.01-3")
    code, _, err = nexo("brain", "pull")
    assert code == 5 and "pre_send é hook de segurança" in err and "shell_exec" in err


def test_skill_canary_by_squad(project: Path, nexo: Runner, tmp_path: Path) -> None:
    skill = "extensions/skills/api-errors"
    repo, pub, _ = brain_repo(
        tmp_path,
        nexo,
        {
            f"{skill}/SKILL.md": "---\nname: api-errors\ndescription: Erros de API (canário)\n---\n# Erros\n",
            f"{skill}/extension.yaml": "name: api-errors\ntype: skill\nscope: org\nrollout:\n  squads: [pagamentos]\n",
        },
    )
    assert use(nexo, repo, pub)[0] == 0
    names = lambda: {s["name"]: s["scope"] for s in json.loads(nexo("--json", "skill", "list")[1])}  # noqa: E731
    assert "api-errors" not in names() and names()["python-standards"] == "embutido"
    nexo("config", "set", "squad", "pagamentos", "--project")
    assert names()["api-errors"] == "org"


def test_migrate_diff_and_baseline_sync(workdir: Path, nexo: Runner, tmp_path: Path) -> None:
    repo, pub, key = brain_repo(tmp_path, nexo, {})
    args = ["--owner-nexo", "@dono", "--owner-security", "@dono", "--stack-owner", "python=@dono"]
    out = nexo("--json", "brain", "migrate", str(repo), *args)[1]
    written = json.loads(out)["written"]
    assert "prompts/reviewer.md" in written and "extensions/skills/secure-coding/extension.yaml" in written
    assert "/extensions/skills/python-standards/ @dono" in (repo / "CODEOWNERS").read_text(encoding="utf-8")
    resign(nexo, repo, key, "2026.10.02-1")
    assert use(nexo, repo, pub)[0] == 0
    assert json.loads(nexo("--json", "brain", "diff")[1])["changes"] == []
    assert {s["scope"] for s in json.loads(nexo("--json", "skill", "list")[1])} == {"org"}
    # o time muda um prompt no cérebro: o diff mostra, e o build detecta a divergência da baseline
    (repo / "prompts/reviewer.md").write_text(
        read_text("prompts/reviewer.md") + "\nSeja rigoroso com LGPD.\n", encoding="utf-8"
    )
    resign(nexo, repo, key, "2026.10.02-2")
    nexo("brain", "pull")
    data = json.loads(nexo("--json", "brain", "diff", "--show", "prompts/reviewer.md")[1])
    assert data["changes"] == [{"path": "prompts/reviewer.md", "status": "alterado no cérebro"}]
    assert "+Seja rigoroso com LGPD." in data["diff"]

"""F25 — dependências e vulnerabilidades em escala: leitura das ferramentas, plano e atualização sem IA."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexo.modules.deps.adapters import scan
from nexo.modules.deps.domain.deps import DepFinding, Vuln, bump_command, plan, semver_change
from tests.conftest import Runner

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "deps"


def read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_parsers_on_real_tool_output() -> None:
    py = scan.parse_pip_audit(read("pip-audit.json"), {"requests"})
    by = {f.package: f for f in py}
    assert set(by) == {"requests", "idna"}  # ruff aparece sem vulnerabilidade
    assert by["requests"].target == "2.33.0" and by["requests"].direct and by["requests"].change == "minor"
    assert by["idna"].target == "3.15" and not by["idna"].direct and by["idna"].change == "major"
    assert any(v.id.startswith("CVE-") for v in by["requests"].vulns)
    npm = {f.package: f for f in scan.parse_npm_audit(read("npm-audit.json"))}
    assert npm["minimist"].worst == "critical" and npm["minimist"].target == "1.2.8"
    assert npm["lodash"].direct and npm["lodash"].target
    outdated = {f.package: f for f in scan.parse_npm_outdated(read("npm-outdated.json"))}
    assert outdated["minimist"].current == "1.2.0" and outdated["minimist"].target == "1.2.8"
    net = scan.parse_dotnet(read("dotnet-vulnerable.json"), read("dotnet-outdated.json"))
    sec = next(f for f in net if f.kind == "security")
    assert (sec.package, sec.current, sec.target, sec.worst) == ("Newtonsoft.Json", "12.0.1", "13.0.4", "high")
    pip = scan.parse_pip_outdated(read("pip-outdated.json"), {"ruff"})
    assert pip[0].package == "ruff" and pip[0].change == "patch"


def test_plan_rules() -> None:
    findings = [
        DepFinding("npm", "minimist", "1.2.0", "1.2.8", "security", True, [Vuln("GHSA-1", "critical")]),
        DepFinding("pip", "idna", "2.10", "3.15", "security", False, [Vuln("CVE-2", "high")]),
        DepFinding("npm", "minimist", "1.2.0", "1.2.8", "outdated", True),  # já coberto pela correção
        DepFinding("pip", "ruff", "0.16.8", "0.16.9", "outdated", True),
        DepFinding("npm", "react", "18.2.0", "19.0.0", "outdated", True),
        DepFinding("npm", "left-pad", "1.0.0", "1.1.0", "outdated", False),  # transitiva: não entra
    ]
    stories = plan(findings)
    titles = [s.title for s in stories]
    assert titles[0].startswith("Corrigir vulnerabilidade em minimist")  # mais grave primeiro
    assert stories[1].human_gate and "versão maior" in stories[1].reason  # correção que muda versão maior
    assert titles[2].startswith("Atualizações de versão menor") and [f.package for f in stories[2].findings] == ["ruff"]
    assert len(stories) == 3  # react (maior) só com --major
    with_major = plan(findings, major=True)
    assert with_major[-2].human_gate and "react" in with_major[-2].title
    assert semver_change("1.2.3", "1.2.3") == "none"


def test_bump_commands_are_safe() -> None:
    assert bump_command("pip", "requests", "2.33.0") == ["nexo:pip-bump", "requests", "2.33.0"]
    assert bump_command("npm", "@scope/pkg", "1.2.8")[:2] == ["npm", "install"]
    assert bump_command("dotnet", "Newtonsoft.Json", "13.0.4") == [
        "dotnet",
        "add",
        "package",
        "Newtonsoft.Json",
        "--version",
        "13.0.4",
    ]
    for bad in ("x; rm -rf /", "pkg$(id)", "a b"):
        with pytest.raises(ValueError):
            bump_command("npm", bad, "1.0.0")


def test_scan_plan_and_token_free_update(  # type: ignore[no-untyped-def]
    project: Path, nexo: Runner, scenario, fast_recipe: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outputs = {
        "pip-audit": read("pip-audit.json"),
        "--outdated": read("pip-outdated.json"),
        "freeze": "requests==2.25.0\nidna==2.10\nruff==0.16.8\n",
    }

    def fake_run(cmd: list[str], cwd: Path) -> str:
        return next((v for k, v in outputs.items() if any(c == k or Path(c).stem == k for c in cmd)), "")

    # ambiente do projeto já criado pelo pipeline (F51: pip audita o que está instalado nele)
    from nexo.modules.quality.adapters.runner import python_env

    venv_python = Path(python_env(project)["venv_python"])
    venv_python.parent.mkdir(parents=True, exist_ok=True)
    venv_python.write_text("", encoding="utf-8")

    monkeypatch.setattr(scan, "_run", fake_run)
    code, out, err = nexo("--json", "deps", "scan")
    assert code == 0, err
    data = json.loads(out)
    assert data["managers"] == ["pip"] and (project / ".nexo" / "deps" / "scan.json").is_file()
    code, out, err = nexo("--json", "deps", "plan")
    assert code == 0, err
    plan_out = json.loads(out)
    fid = plan_out["feature"]
    assert [s["title"].split(" (")[0] for s in plan_out["stories"]] == [
        "Corrigir vulnerabilidade em idna",  # mesma severidade: ordem alfabética
        "Corrigir vulnerabilidade em requests",
        "Atualizações de versão menor e de correção",
    ]
    assert plan_out["human_gates"] == [f"US-{fid[2:]}-1"]  # idna 2 → 3: versão maior
    task = (project / ".nexo" / "tasks" / f"T-{fid[2:]}-2.yaml").read_text(encoding="utf-8")
    assert "dependency" in task and "requests" in task and "2.33.0" in task

    # execução: o Nexo aplica a atualização; nenhuma chamada de agente (cenário vazio)
    from nexo.modules.deps.app import deps

    applied: list[dict[str, str]] = []

    def fake_apply(worktree: Path, script: dict[str, str]) -> tuple[bool, str]:
        applied.append(script)
        pyproject = worktree / "pyproject.toml"
        pyproject.write_text(
            pyproject.read_text(encoding="utf-8") + f"\n# {script['package']}>={script['version']}\n", encoding="utf-8"
        )
        return True, "ok"

    monkeypatch.setattr(deps, "apply_script", fake_apply)
    scenario([])
    code, out, err = nexo("--json", "run", "story", f"US-{fid[2:]}-2")
    assert code == 0, err
    rep = json.loads(out)
    assert applied == [{"manager": "pip", "package": "requests", "version": "2.33.0"}]
    assert rep["state"] == "AguardandoHumano"  # dependência é área de aprovação humana por padrão
    assert {q["id"]: q["status"] for q in rep["quality"]}["unit"] == "passed"
    logs = json.loads(nexo("--json", "logs", "-n", "300")[1])
    types = {e["type"] for e in logs}
    assert "deps.applied" in types and "story.reviews_skipped" in types and "llm.result" not in types

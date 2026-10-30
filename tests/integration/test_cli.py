from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from nexo import __version__
from tests.conftest import EXAMPLES, Runner


def test_banner_without_project(nexo: Runner) -> None:
    code, out, _ = nexo()
    assert code == 0
    assert "███╗" in out and "nenhum (rode `nexo init`)" in out
    assert "Orçamento" in out


def test_banner_json_and_quiet(nexo: Runner) -> None:
    code, out, _ = nexo("--json")
    data = json.loads(out)
    assert code == 0 and data["version"] and set(data["agents"]) == {"claude", "copilot", "devin"}
    assert nexo("--quiet")[1] == ""


def test_root_help_flag_uses_custom_help(nexo: Runner) -> None:
    code, out, _ = nexo("--help")
    assert code == 0 and "pm refine" in out


def test_help_for_specific_command(nexo: Runner) -> None:
    code, out, _ = nexo("help", "eval")
    assert code == 0 and "nexo eval skill" in out and "[fase" not in out
    code, _, err = nexo("help", "nope")
    assert code == 2 and "desconhecido" in err


def test_help_json_lists_all(nexo: Runner) -> None:
    from nexo.cli.registry import COMMANDS

    data = json.loads(nexo("--json", "help")[1])
    assert len(data) == len([s for s in COMMANDS if not s.hidden])  # atalhos antigos ficam fora da ajuda


@pytest.mark.parametrize(
    "argv",
    [["learn", "status"], ["eval", "report"], ["metrics"], ["budget", "show"], ["status"]],
)
def test_all_phases_available(nexo: Runner, argv: list[str]) -> None:
    code, _, err = nexo(*argv)
    assert code != 3, err


def test_no_command_left_for_future_phases() -> None:
    from nexo.cli.registry import COMMANDS

    assert all(spec.available for spec in COMMANDS)


def test_version(nexo: Runner) -> None:
    code, out, _ = nexo("version")
    assert code == 0 and f"nexo {__version__}" in out
    assert json.loads(nexo("--json", "version")[1])["mode"] == "package"


def test_lang_en(nexo: Runner) -> None:
    code, out, _ = nexo("--lang", "en", "help")
    assert (code == 0 and "GLOBAL OPTIONS" in out) or "Usage" in out


def test_config_set_get_list(nexo: Runner, nexo_home: Path) -> None:
    assert nexo("config", "set", "profile", "economico")[0] == 0
    _code, out, _ = nexo("--json", "config", "get", "profile")
    data = json.loads(out)
    assert data == {"key": "profile", "value": "economico", "origin": "personal", "locked_by": None}
    listing = json.loads(nexo("--json", "config", "list")[1])
    assert listing["max_parallel"] == {"value": 3, "origin": "default"}
    assert nexo("config", "unset", "profile")[0] == 0
    assert nexo("config", "unset", "profile")[0] == 1
    assert nexo("config", "get", "nao.existe")[0] == 1


def test_config_validation(nexo: Runner) -> None:
    code, _, err = nexo("config", "set", "profile", "barato")
    assert code == 1 and "permitidos" in err
    assert nexo("config", "set", "max_parallel", "99")[0] == 1
    assert nexo("config", "set", "max_parallel", "2")[0] == 0


def test_config_set_project_requires_project(nexo: Runner) -> None:
    code, _, err = nexo("config", "set", "profile", "economico", "--project")
    assert code == 1 and "nexo init" in err


def test_locked_key_is_policy_violation(nexo: Runner, nexo_home: Path) -> None:
    org = nexo_home / "brain" / "org"
    org.mkdir(parents=True)
    (org / "config.yaml").write_text("profile: balanceado\nlocked: [profile]\n", encoding="utf-8")
    code, _, err = nexo("config", "set", "profile", "economico")
    assert code == 5 and "bloqueada" in err
    data = json.loads(nexo("--json", "config", "get", "profile")[1])
    assert data["locked_by"] == "org" and data["value"] == "balanceado"
    events = json.loads(nexo("--json", "logs", "-n", "20")[1])
    assert any(e["type"] == "policy.violation" and e["payload"]["rule"] == "locked-key" for e in events)


def test_secret_value_is_refused_and_masked_in_audit(nexo: Runner, nexo_home: Path) -> None:
    token = "ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD"
    code, _, _ = nexo("config", "set", "notes", f"token={token}")
    assert code == 5
    assert not (nexo_home / "config.yaml").exists() or token not in (nexo_home / "config.yaml").read_text(
        encoding="utf-8"
    )
    db = sqlite3.connect(nexo_home / "nexo.db")
    dump = "\n".join(row[0] for row in db.execute("SELECT payload FROM events"))
    assert token not in dump and "[REDACTED" in dump


def test_audit_verify_and_tamper(nexo: Runner, nexo_home: Path) -> None:
    nexo("config", "set", "profile", "economico")
    code, out, _ = nexo("audit", "verify")
    assert code == 0 and "íntegra" in out
    db = sqlite3.connect(nexo_home / "nexo.db")
    db.execute("UPDATE events SET payload = '{\"forjado\": true}' WHERE seq = 2")
    db.commit()
    db.close()
    code, _, err = nexo("audit", "verify")
    assert code == 7 and "COMPROMETIDA" in err


def test_logs_filter_and_table(nexo: Runner) -> None:
    nexo("config", "set", "lang", "pt-BR")
    code, out, _ = nexo("logs", "-n", "5")
    assert code == 0 and "config.set" in out
    assert json.loads(nexo("--json", "logs", "no-such-ref")[1]) == []


@pytest.mark.parametrize(
    ("example", "stacks"),
    [
        ("dotnet-api", ["dotnet"]),
        ("react-web", ["react"]),
        ("python-svc", ["python"]),
        ("java-api", ["java"]),
        ("angular-web", ["angular"]),
        ("flutter-app", ["flutter"]),  # android/build.gradle.kts é do Flutter, não vira java
    ],
)
def test_init_detects_example_stacks(nexo: Runner, workdir: Path, example: str, stacks: list[str]) -> None:
    target = workdir / example
    shutil.copytree(EXAMPLES / example, target)
    code, out, _ = nexo("--json", "init", str(target))
    data = json.loads(out)
    assert code == 0 and data["created"] and data["stacks"] == stacks
    cfg = (target / ".nexo" / "nexo.yaml").read_text(encoding="utf-8")
    assert "data_class: confidential" in cfg
    assert "runs/" in (target / ".nexo" / ".gitignore").read_text(encoding="utf-8").splitlines()
    again = json.loads(nexo("--json", "init", str(target))[1])
    assert again["created"] is False


def test_project_layer_and_banner(nexo: Runner, workdir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    shutil.copytree(EXAMPLES / "python-svc", workdir / "svc")
    monkeypatch.chdir(workdir / "svc")
    nexo("init")
    assert nexo("config", "set", "profile", "maxima-qualidade", "--project")[0] == 0
    data = json.loads(nexo("--json", "config", "get", "profile")[1])
    assert data["origin"] == "project"
    _code, out, _ = nexo()
    assert "svc (python" in out


def test_doctor_json(nexo: Runner) -> None:
    code, out, _ = nexo("--json", "doctor")
    data = json.loads(out)
    names = {c["name"] for c in data["checks"]}
    assert {"python", "git", "claude", "copilot", "devin", "audit", "brain"} <= names
    assert code == (0 if data["ok"] else 1)


def test_doctor_table(nexo: Runner) -> None:
    _code, out, _ = nexo("doctor")
    assert "Diagnóstico" in out


def test_global_option_requires_value(nexo: Runner) -> None:
    code, _, _err = nexo("--lang")
    assert code == 2


def test_usage_error_exit_2(nexo: Runner) -> None:
    code, _, _ = nexo("config", "set")
    assert code == 2


def test_install_command(nexo: Runner, nexo_home: Path) -> None:
    code, _out, _ = nexo("install")
    assert code == 0 and (nexo_home / "bin").is_dir()


def test_update_package_mode_without_manifest(nexo: Runner) -> None:
    code, out, _ = nexo("update")
    assert code == 0 and "pip install --upgrade" in out


def test_uninstall_package_purge(nexo: Runner, nexo_home: Path) -> None:
    nexo("install")
    code, out, _ = nexo("uninstall", "--purge", "--yes")
    assert code == 0 and "pip uninstall nexo" in out and not nexo_home.exists()


def test_redirected_cp1252_output_is_switched_to_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import sys

    from nexo.cli.main import utf8_streams

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stream)
    utf8_streams()
    print("███ Orçamento", file=stream)  # quebrava com UnicodeEncodeError no Windows com saída redirecionada
    stream.flush()
    assert raw.getvalue().decode("utf-8").startswith("███ Orçamento")


@pytest.mark.parametrize(
    ("new", "old"),
    [
        (["cost", "budget", "show"], ["budget", "show"]),
        (["cost", "usage", "show"], ["usage", "show"]),
        (["cost", "pricing", "show"], ["pricing", "show"]),
        (["metrics"], ["metrics"]),
        (["quality", "standards", "rules", "--stack", "python"], ["standards", "rules", "--stack", "python"]),
        (["extensions", "skill", "list"], ["skill", "list"]),
        (["admin", "flags", "list"], ["flags", "list"]),
        (["admin", "killswitch", "status"], ["killswitch", "status"]),
        (["inbox"], ["plan", "inbox"]),
    ],
)
def test_merged_and_old_names_both_work(nexo: Runner, workdir: Path, new: list[str], old: list[str]) -> None:
    """o nome novo funciona e o antigo continua valendo (roteiros e documentação já publicados)."""
    nexo("init")
    assert nexo(*new)[0] == 0, new
    assert nexo(*old)[0] == 0, old

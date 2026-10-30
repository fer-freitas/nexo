"""Adaptador genérico (Copilot/Devin) e sessão de subprocesso com uma CLI simulada."""

from __future__ import annotations

import itertools
import sys
import textwrap
from pathlib import Path

from nexo.kernel.models.agent import DONE, AgentRequest
from nexo.modules.execution.adapters.agents.generic import GenericTextAdapter


def fake_cli(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "cli.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    return script


def run(adapter: GenericTextAdapter, script: Path, tmp_path: Path, timeout: float = 20):  # type: ignore[no-untyped-def]
    adapter.spec = {**adapter.spec, "command": [sys.executable, str(script), "{prompt}", "{model}"]}
    req = AgentRequest("developer", "olá", str(tmp_path), "gpt-5", timeout_s=timeout, denied_tools=("shell(rm)",))
    session = adapter.start(req)
    events = list(session.events())
    assert events[-1].kind == DONE
    return events, events[-1].data["outcome"]


def test_success_with_estimated_usage(tmp_path: Path) -> None:
    script = fake_cli(tmp_path, "import sys\nprint('feito', sys.argv[1], sys.argv[2])\n")
    _events, outcome = run(GenericTextAdapter("copilot"), script, tmp_path)
    assert outcome.status == "success" and "feito olá gpt-5" in outcome.text
    assert outcome.usage[0].unit == "premium_request" and outcome.usage[0].estimated


def test_rate_limit_detected_from_stderr(tmp_path: Path) -> None:
    script = fake_cli(
        tmp_path, "import sys\nsys.stderr.write('Error: quota exceeded for premium requests')\nsys.exit(1)\n"
    )
    _, outcome = run(GenericTextAdapter("copilot"), script, tmp_path)
    assert outcome.status == "rate_limited"


def test_auth_error(tmp_path: Path) -> None:
    script = fake_cli(tmp_path, "import sys\nprint('You are not logged in')\nsys.exit(1)\n")
    _, outcome = run(GenericTextAdapter("devin"), script, tmp_path)
    assert outcome.status == "error" and outcome.error_kind == "auth"
    assert outcome.usage[0].unit == "acu"


def test_timeout_kills_process(tmp_path: Path) -> None:
    script = fake_cli(tmp_path, "import time\nprint('start', flush=True)\ntime.sleep(30)\n")
    _, outcome = run(GenericTextAdapter("copilot"), script, tmp_path, timeout=1.5)
    assert outcome.status == "timeout"


def test_terminate(tmp_path: Path) -> None:
    script = fake_cli(tmp_path, "import time\nfor i in range(100):\n    print(i, flush=True)\n    time.sleep(0.1)\n")
    adapter = GenericTextAdapter("copilot")
    adapter.spec = {**adapter.spec, "command": [sys.executable, str(script)]}
    session = adapter.start(AgentRequest("developer", "x", str(tmp_path), "gpt-5"))
    outcome = None
    for i, event in enumerate(session.events()):
        if i == 2:
            session.terminate()
        if event.kind == DONE:
            outcome = event.data["outcome"]
    assert outcome is not None and outcome.status == "interrupted"


def test_missing_executable(tmp_path: Path) -> None:
    adapter = GenericTextAdapter("copilot")
    adapter.spec = {**adapter.spec, "command": ["/nonexistent/copilot-cli", "{prompt}"]}
    events = list(adapter.start(AgentRequest("developer", "x", str(tmp_path), "m")).events())
    assert events[-1].data["outcome"].status == "error"


def test_command_flags_and_capabilities() -> None:
    adapter = GenericTextAdapter("copilot")
    req = AgentRequest(
        "developer",
        "p",
        "/w",
        "gpt-5",
        allowed_tools=("write",),
        denied_tools=("shell(rm)",),
        mcp_config={"mcpServers": {}},
    )
    cmd = adapter.command(req, "copilot")
    assert cmd[:3] == ["copilot", "-p", "p"]
    assert "--deny-tool" in cmd and "shell(git push)" in cmd and "shell(rm)" in cmd
    assert "--allow-tool" in cmd and "--additional-mcp-config" in cmd
    caps = adapter.capabilities()
    assert caps.verified and not caps.native_hooks and caps.usage_unit == "premium_request"  # verificado em 1.0.88
    assert adapter.auth_status().authenticated is None
    devin_cmd = GenericTextAdapter("devin").command(AgentRequest("d", "p", "/w", "m"), "devin")
    # o Devin só grava arquivos no `-p`; o prompt diz que o Nexo roda os testes
    assert devin_cmd[2].startswith("p\n") and "não executa comandos" in devin_cmd[2]
    assert "não executa comandos" not in " ".join(adapter.command(AgentRequest("d", "p", "/w", "m"), "copilot"))
    assert [devin_cmd[0], devin_cmd[1], *devin_cmd[3:]] == [
        "devin",
        "-p",
        "--respect-workspace-trust",
        "false",
        "--permission-mode",
        "accept-edits",
    ]


def test_permissions_by_profile_found_in_real_runs() -> None:
    """Teste real: no modo não interativo o Copilot exige --allow-all-tools (a política entra pelas negações) e o
    Devin precisa do modo de permissão que aceita edições; somente leitura nunca recebe escrita nem shell."""
    copilot = GenericTextAdapter("copilot")
    req = AgentRequest("reviewer", "p", "/w", "auto")
    writer = copilot.command(req, "copilot", "code-writer")
    reader = copilot.command(req, "copilot", "read-only")
    assert "--allow-all-tools" in writer and "shell(sudo)" in writer and "write" not in writer
    pairs = list(itertools.pairwise(reader))
    assert ("--deny-tool", "write") in pairs and ("--deny-tool", "shell") in pairs
    devin = GenericTextAdapter("devin")
    assert devin.command(req, "devin", "read-only")[-2:] == ["--permission-mode", "auto"]
    assert devin.command(req, "devin", "test-writer")[-2:] == ["--permission-mode", "accept-edits"]


def test_model_unavailable_and_blocked_tools_are_errors(tmp_path: Path) -> None:
    unavailable = fake_cli(
        tmp_path,
        """
        import sys
        sys.stderr.write('Error: Model "gpt-5" from --model flag is not available.')
        sys.exit(1)
    """,
    )
    _, outcome = run(GenericTextAdapter("copilot"), unavailable, tmp_path)
    assert outcome.status == "error" and outcome.error_kind == "model_unavailable"
    blocked = fake_cli(
        tmp_path,
        """
        print("x Create src/a.py")
        print("  Permission denied and could not request permission from user.")
    """,
    )
    _, outcome = run(GenericTextAdapter("copilot"), blocked, tmp_path)
    assert outcome.status == "error" and outcome.error_kind == "policy_violation"  # saída 0, mas nada foi feito

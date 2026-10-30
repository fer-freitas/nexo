"""Testes de contrato do adaptador Claude contra gravações reais."""

from __future__ import annotations

from pathlib import Path

import pytest

from nexo.kernel.models.agent import FILE_CHANGE, INIT, QUOTA, RATE_LIMITED, TOOL_CALL, AgentRequest
from nexo.kernel.runtime.resources import load_yaml
from nexo.modules.execution.adapters.agents.base import Patterns
from nexo.modules.execution.adapters.agents.claude import ClaudeAdapter, ClaudeParser

REC = Path(__file__).resolve().parents[1] / "fixtures" / "recordings" / "claude"
PATTERNS = Patterns(load_yaml("agents/claude/patterns.yaml"))


def replay(name: str, returncode: int = 0, stderr: str = ""):  # type: ignore[no-untyped-def]
    parser = ClaudeParser(PATTERNS)
    events = []
    for line in (REC / name).read_text(encoding="utf-8").splitlines():
        events.extend(parser.feed(line))
    return events, parser.finish(returncode, stderr)


def test_simple_success() -> None:
    events, outcome = replay("2.1.281/simple.jsonl")
    assert events[0].kind == INIT and events[0].data["model"].startswith("claude-haiku")
    assert outcome.status == "success" and "OK" in outcome.text
    units = {u.unit for u in outcome.usage}
    assert {"tokens_in", "tokens_out"} <= units
    assert sum(u.api_equiv_usd for u in outcome.usage) > 0


def test_edit_emits_tool_call_and_file_change() -> None:
    events, outcome = replay("2.1.281/edit.jsonl")
    kinds = [e.kind for e in events]
    assert TOOL_CALL in kinds and FILE_CHANGE in kinds and QUOTA in kinds
    assert outcome.files_changed == ["/WORKDIR/hello.txt"]
    assert outcome.tool_calls == 1
    assert outcome.quota_utilization is not None


def test_denied_tool_not_offered() -> None:
    events, outcome = replay("2.1.281/denied.jsonl")
    assert events[0].data["tools"] == ["Read"]
    assert outcome.status == "success" and "DENIED" in outcome.text


def test_structured_output() -> None:
    _, outcome = replay("2.1.281/structured.jsonl")
    assert outcome.structured == {"kind": "backend", "size": "M"}


def test_rate_limited_synthetic() -> None:
    events, outcome = replay("synthetic/rate_limited.jsonl")
    assert RATE_LIMITED in [e.kind for e in events]
    assert outcome.status == "rate_limited" and outcome.reset_at == 1790238600


@pytest.mark.parametrize(
    ("stderr", "status", "kind"),
    [
        ("Error: Claude AI usage limit reached", "rate_limited", None),
        ("Invalid API key · Please run /login", "error", "auth"),
        ("Prompt is too long", "error", "context_limit"),
        ("segfault", "error", "technical"),
    ],
)
def test_no_result_classification(stderr: str, status: str, kind: str | None) -> None:
    parser = ClaudeParser(PATTERNS)
    outcome = parser.finish(1, stderr)
    assert outcome.status == status and outcome.error_kind == kind


def test_error_result_classification() -> None:
    parser = ClaudeParser(PATTERNS)
    parser.feed(
        '{"type":"result","subtype":"error_during_execution","is_error":true,"api_error_status":401,'
        '"result":"auth failed","usage":{}}'
    )
    assert parser.finish(1, "").error_kind == "auth"
    parser = ClaudeParser(PATTERNS)
    parser.feed(
        '{"type":"result","subtype":"error","is_error":true,"result":"limit |1790238600","usage":{},'
        '"api_error_status":429}'
    )
    out = parser.finish(1, "")
    assert out.status == "rate_limited" and out.reset_at == 1790238600
    assert ClaudeParser(PATTERNS).feed("plain text line")[0].kind == "message"
    assert ClaudeParser(PATTERNS).feed("   ") == []


def test_command_building() -> None:
    adapter = ClaudeAdapter()
    req = AgentRequest(
        "developer",
        "p",
        "/w",
        "sonnet",
        mcp_config={"mcpServers": {"nexo": {"command": "x"}}},
        settings={"hooks": {}},
        output_schema={"type": "object"},
        plugin_dirs=("/plug",),
    )
    cmd = adapter.command(req, "code-writer")
    assert cmd[:2] == ["claude", "-p"] and "--strict-mcp-config" in cmd
    assert cmd[cmd.index("--model") + 1] == "sonnet"
    assert "mcp__nexo" in cmd[cmd.index("--allowedTools") + 1]
    assert "WebFetch" in cmd[cmd.index("--disallowedTools") + 1]
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"
    assert "--json-schema" in cmd and "--plugin-dir" in cmd and "--settings" in cmd
    ro = adapter.command(AgentRequest("classifier", "p", "/w", "haiku"), "read-only")
    assert ro[ro.index("--permission-mode") + 1] == "dontAsk"
    assert ro[ro.index("--tools") + 1] == "Read,Glob,Grep"
    assert "p" not in ro[1:]  # prompt vai por stdin, nunca na linha de comando
    assert adapter.capabilities().native_hooks

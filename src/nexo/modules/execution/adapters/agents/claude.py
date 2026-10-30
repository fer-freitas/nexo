"""Adaptador Claude Code CLI."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

from nexo.kernel.models.agent import (
    ERR_TECHNICAL,
    FILE_CHANGE,
    INIT,
    MESSAGE,
    QUOTA,
    RATE_LIMITED,
    TOOL_CALL,
    TOOL_RESULT,
    AgentEvent,
    AgentOutcome,
    AgentRequest,
    AuthStatus,
    Capabilities,
    ToolInfo,
    UsageRecord,
)
from nexo.kernel.runtime.resources import load_yaml
from nexo.kernel.runtime.system import probe, windows_command
from nexo.modules.execution.adapters.agents.base import ERROR_KIND, Patterns, SubprocessSession

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
ALLOWED_STATUSES = {"allowed", "allowed_warning"}
RESET_IN_TEXT = re.compile(r"\|(\d{10})\b")


class ClaudeParser:
    def __init__(self, patterns: Patterns) -> None:
        self.patterns = patterns
        self.result: dict[str, Any] | None = None
        self.reset_at: float | None = None
        self.rejected = False
        self.utilization: float | None = None
        self.texts: list[str] = []
        self.files: list[str] = []
        self.tool_calls = 0

    def feed(self, line: str) -> list[AgentEvent]:
        line = line.strip()
        if not line:
            return []
        try:
            data = json.loads(line)
        except ValueError:
            return [AgentEvent(MESSAGE, {"text": line})]
        kind = data.get("type")
        if kind == "system" and data.get("subtype") == "init":
            return [AgentEvent(INIT, {k: data.get(k) for k in ("model", "tools", "session_id", "permissionMode")})]
        if kind == "assistant":
            return self._assistant(data)
        if kind == "user":
            out = []
            for block in (data.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    out.append(
                        AgentEvent(TOOL_RESULT, {"id": block.get("tool_use_id"), "error": block.get("is_error")})
                    )
            return out
        if kind == "rate_limit_event":
            return self._rate_limit(data.get("rate_limit_info") or {})
        if kind == "result":
            self.result = data
        return []

    def _assistant(self, data: dict[str, Any]) -> list[AgentEvent]:
        out: list[AgentEvent] = []
        for block in (data.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                self.texts.append(str(block.get("text", "")))
                out.append(AgentEvent(MESSAGE, {"text": block.get("text", "")}))
            elif block.get("type") == "tool_use":
                self.tool_calls += 1
                name = str(block.get("name"))
                tool_input = block.get("input") or {}
                out.append(AgentEvent(TOOL_CALL, {"id": block.get("id"), "name": name, "input": tool_input}))
                path = tool_input.get("file_path") or tool_input.get("notebook_path")
                if name in WRITE_TOOLS and path:
                    self.files.append(str(path))
                    out.append(AgentEvent(FILE_CHANGE, {"path": str(path)}))
        return out

    def _rate_limit(self, info: dict[str, Any]) -> list[AgentEvent]:
        status = str(info.get("status", ""))
        resets = info.get("resetsAt")
        windows = info.get("unifiedWindows") or {}
        utils = [float(w.get("utilization", 0)) for w in windows.values() if isinstance(w, dict)]
        self.utilization = max(utils) if utils else None
        events = [AgentEvent(QUOTA, {"status": status, "resets_at": resets, "utilization": self.utilization})]
        if status and status not in ALLOWED_STATUSES:
            self.rejected = True
            self.reset_at = float(resets) if resets else None
            events.append(AgentEvent(RATE_LIMITED, {"reset_at": self.reset_at}))
        return events

    def finish(self, returncode: int, stderr: str) -> AgentOutcome:
        res = self.result
        if res is None:
            kind = self.patterns.classify(stderr)
            if self.rejected or kind == "rate_limit":
                return AgentOutcome("rate_limited", None, "cota esgotada", reset_at=self.reset_at)
            error_kind = ERROR_KIND.get(kind or "", ERR_TECHNICAL)
            return AgentOutcome("error", error_kind, (stderr.strip() or f"saída {returncode} sem resultado")[-500:])
        usage = _usage(res)
        text = str(res.get("result") or "\n".join(self.texts))
        outcome = AgentOutcome(
            text=text,
            usage=usage,
            structured=res.get("structured_output") if isinstance(res.get("structured_output"), dict) else None,
            files_changed=sorted(set(self.files)),
            tool_calls=self.tool_calls,
            quota_utilization=self.utilization,
        )
        status_code = res.get("api_error_status")
        if not res.get("is_error") and res.get("subtype") == "success":
            outcome.status = "success"
            return outcome
        kind = self.patterns.classify(text + "\n" + stderr, int(status_code) if status_code else None)
        if self.rejected or kind == "rate_limit":
            outcome.status = "rate_limited"
            outcome.message = "cota esgotada"
            match = RESET_IN_TEXT.search(text)
            outcome.reset_at = self.reset_at or (float(match.group(1)) if match else None)
            return outcome
        outcome.status = "error"
        outcome.error_kind = ERROR_KIND.get(kind or "", ERR_TECHNICAL)
        outcome.message = (text or stderr or str(res.get("subtype")))[-500:]
        return outcome


def _usage(res: dict[str, Any]) -> list[UsageRecord]:
    u = res.get("usage") or {}
    cost = float(res.get("total_cost_usd") or 0.0)
    records = [
        UsageRecord("tokens_in", float(u.get("input_tokens") or 0)),
        UsageRecord("tokens_out", float(u.get("output_tokens") or 0), api_equiv_usd=cost),
        UsageRecord("cache_read", float(u.get("cache_read_input_tokens") or 0)),
        UsageRecord("cache_write", float(u.get("cache_creation_input_tokens") or 0)),
    ]
    return [r for r in records if r.quantity or r.api_equiv_usd]


# Ferramentas nativas por perfil de permissão (o hook PreToolUse refina caminhos e comandos).
TOOLS_BY_PROFILE = {
    "read-only": ("Read", "Glob", "Grep"),
    "test-writer": ("Read", "Glob", "Grep", "Write", "Edit", "MultiEdit", "Bash", "TodoWrite"),
    "code-writer": ("Read", "Glob", "Grep", "Write", "Edit", "MultiEdit", "Bash", "TodoWrite"),
    # o hook restringe a escrita a docs/ e README; sem esta linha o writer caía em somente leitura
    "doc-writer": ("Read", "Glob", "Grep", "Write", "Edit", "MultiEdit", "TodoWrite"),
}
ALWAYS_DENIED = ("WebFetch", "WebSearch", "Task", "Agent")


class ClaudeAdapter:
    name = "claude"
    vendor = "anthropic"
    executable = "claude"

    def __init__(self, brain_dir: Path | None = None) -> None:
        self.patterns = Patterns(load_yaml("agents/claude/patterns.yaml", brain_dir))

    def detect(self) -> ToolInfo:
        p = probe(self.executable)
        return ToolInfo(self.name, p.installed, p.version, p.path)

    def auth_status(self) -> AuthStatus:
        info = self.detect()
        if not info.installed or info.path is None:
            return AuthStatus(False, "não instalado")
        try:
            proc = subprocess.run(
                windows_command([info.path, "auth", "status"]),
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
                encoding="utf-8",
            )
            data = json.loads(proc.stdout or "{}")
        except (OSError, subprocess.TimeoutExpired, ValueError):
            return AuthStatus(None, "não foi possível verificar")
        return AuthStatus(bool(data.get("loggedIn")), str(data.get("authMethod") or ""))

    def capabilities(self) -> Capabilities:
        return Capabilities(
            verified=True,
            structured_output=True,
            native_hooks=True,
            guard_proven=True,
            mcp=True,
            skills=True,
            images=True,
        )

    def command(self, req: AgentRequest, profile: str) -> list[str]:
        tools = list(req.allowed_tools or TOOLS_BY_PROFILE.get(profile, TOOLS_BY_PROFILE["read-only"]))
        denied = list(dict.fromkeys([*ALWAYS_DENIED, *req.denied_tools]))
        allowed = list(tools)
        if req.mcp_config:
            allowed += [f"mcp__{name}" for name in (req.mcp_config.get("mcpServers") or {})]
        cmd = [
            self.executable,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            req.model,
            "--no-session-persistence",
            "--permission-prompts",
            "none",
            "--permission-mode",
            "acceptEdits" if profile != "read-only" else "dontAsk",
            "--strict-mcp-config",
            "--tools",
            ",".join(tools) if tools else "",
            "--allowedTools",
            ",".join(allowed),
            "--disallowedTools",
            ",".join(denied),
        ]
        if req.mcp_config:
            cmd += ["--mcp-config", json.dumps(req.mcp_config)]
        if req.settings:
            cmd += ["--settings", json.dumps(req.settings)]
        if req.output_schema:
            cmd += ["--json-schema", json.dumps(req.output_schema)]
        for plugin in req.plugin_dirs:
            cmd += ["--plugin-dir", plugin]
        return cmd

    def start(self, req: AgentRequest, profile: str = "code-writer") -> SubprocessSession:
        info = self.detect()
        cmd = self.command(req, profile)
        if info.path:
            cmd[0] = info.path
        # O prompt vai por stdin: sem limite de linha de comando e fora da lista de processos.
        return SubprocessSession(cmd, req, ClaudeParser(self.patterns), stdin_text=req.prompt)

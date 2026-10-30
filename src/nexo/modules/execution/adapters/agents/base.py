"""Infraestrutura comum dos adaptadores de agente baseados em subprocesso."""

from __future__ import annotations

import os
import queue
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol

from nexo.kernel.models.agent import (
    DONE,
    ERR_AUTH,
    ERR_CONTEXT,
    ERR_MODEL,
    ERR_OVERLOADED,
    ERR_POLICY,
    ERR_TECHNICAL,
    ERR_TIMEOUT,
    ERROR,
    AgentEvent,
    AgentOutcome,
    AgentRequest,
    AuthStatus,
    Capabilities,
    ToolInfo,
)
from nexo.kernel.runtime.system import windows_command
from nexo.kernel.security.environment import filter_env


class Patterns:
    """Padrões de detecção carregados de `agents/<tool>/patterns.yaml`."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._text = {k: [re.compile(p) for p in (v or {}).get("text", [])] for k, v in data.items()}
        self._status = {k: {int(s) for s in (v or {}).get("api_error_status", [])} for k, v in data.items()}

    def classify(self, text: str = "", status: int | None = None) -> str | None:
        for kind in ("rate_limit", "auth_error", "context_limit", "overloaded", "model_unavailable", "tool_blocked"):
            if status is not None and status in self._status.get(kind, set()):
                return kind
            if text and any(p.search(text) for p in self._text.get(kind, [])):
                return kind
        return None


ERROR_KIND = {
    "auth_error": ERR_AUTH,
    "context_limit": ERR_CONTEXT,
    "overloaded": ERR_OVERLOADED,
    "model_unavailable": ERR_MODEL,
    "tool_blocked": ERR_POLICY,  # a CLI negou as ferramentas e não tinha como pedir aprovação: nada foi feito
}


class Parser(Protocol):
    def feed(self, line: str) -> list[AgentEvent]: ...

    def finish(self, returncode: int, stderr: str) -> AgentOutcome: ...


class AgentSession(Protocol):
    def events(self) -> Iterator[AgentEvent]: ...

    def terminate(self) -> None: ...


class AgentAdapter(Protocol):
    name: str
    vendor: str

    def detect(self) -> ToolInfo: ...

    def auth_status(self) -> AuthStatus: ...

    def capabilities(self) -> Capabilities: ...

    def start(self, req: AgentRequest, profile: str = ...) -> AgentSession: ...


def kill_tree(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, check=False)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()


class SubprocessSession:
    """Executa a CLI, lê stdout linha a linha com timeout global e permite interrupção."""

    def __init__(
        self,
        cmd: list[str],
        req: AgentRequest,
        parser: Parser,
        stdin_text: str | None = None,
    ) -> None:
        self.cmd = cmd
        self.req = req
        self.parser = parser
        self.stdin_text = stdin_text
        self._proc: subprocess.Popen[str] | None = None
        self._terminated = False

    def terminate(self) -> None:
        self._terminated = True
        if self._proc is not None:
            kill_tree(self._proc)

    def events(self) -> Iterator[AgentEvent]:
        stderr_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8")  # noqa: SIM115 — fechado no finally
        env, _removed = filter_env(os.environ, self.req.env_allow)  # nunca herda o ambiente inteiro
        env.update(self.req.env)
        cmd, cwd = self.cmd, self.req.workdir
        if self.req.container:  # S-04: a CLI roda no contêiner; o ambiente filtrado entra por nome (-e VAR)
            from nexo.kernel.security.isolation import container_command

            c = self.req.container
            base = {"PATH", "HOME", "LANG", "TMPDIR", "TEMP", "SYSTEMROOT"}
            cmd = container_command(
                self.cmd,
                str(Path(self.req.workdir).resolve()),
                str(c["image"]),
                [k for k in env if k not in base],
                str(c.get("engine", "docker")),
                str(c.get("network", "nexo-egress")),
                str(c.get("proxy", "http://nexo-egress:3128")),
            )
        cmd = windows_command(cmd)  # Windows: atalho .cmd do npm vira `node <script>` (prompt com quebra de linha)
        kwargs: dict[str, Any] = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        try:
            # lista de argumentos, sem shell; no contêiner, imagem e nomes de variável validados (isolation.py)
            self._proc = subprocess.Popen(
                cmd,  # nosemgrep
                cwd=cwd,
                stdin=subprocess.PIPE if self.stdin_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=stderr_file,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                **kwargs,
            )
        except OSError as exc:
            stderr_file.close()
            yield AgentEvent(ERROR, {"kind": ERR_TECHNICAL, "message": str(exc)})
            yield AgentEvent(DONE, {"outcome": AgentOutcome("error", ERR_TECHNICAL, str(exc))})
            return
        proc = self._proc
        if self.stdin_text is not None and proc.stdin is not None:
            threading.Thread(target=_write_stdin, args=(proc, self.stdin_text), daemon=True).start()
        lines: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=_pump, args=(proc, lines), daemon=True).start()
        deadline = time.monotonic() + self.req.timeout_s
        timed_out = False
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    kill_tree(proc)
                    break
                try:
                    line = lines.get(timeout=min(remaining, 0.5))
                except queue.Empty:
                    if self._terminated:
                        break
                    continue
                if line is None:
                    break
                yield from self.parser.feed(line)
            proc.wait(timeout=10)
            stderr_file.seek(0)
            stderr = stderr_file.read()[-20000:]
            if timed_out:
                outcome = AgentOutcome("timeout", ERR_TIMEOUT, f"tempo limite de {self.req.timeout_s:.0f}s excedido")
            elif self._terminated:
                outcome = AgentOutcome("interrupted", None, "interrompido pelo Nexo")
            else:
                outcome = self.parser.finish(proc.returncode or 0, stderr)
            yield AgentEvent(DONE, {"outcome": outcome})
        finally:
            kill_tree(proc)
            stderr_file.close()


def _write_stdin(proc: subprocess.Popen[str], text: str) -> None:
    try:
        if proc.stdin is None:
            return
        proc.stdin.write(text)
        proc.stdin.close()
    except (BrokenPipeError, OSError, ValueError):
        pass


def _pump(proc: subprocess.Popen[str], lines: queue.Queue[str | None]) -> None:
    if proc.stdout is None:
        lines.put(None)
        return
    for line in proc.stdout:
        lines.put(line.rstrip("\n"))
    lines.put(None)


def write_temp_json(data: dict[str, Any], prefix: str) -> Path:
    import json

    fd, name = tempfile.mkstemp(prefix=prefix, suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return Path(name)

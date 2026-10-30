"""Agente simulado dirigido por cenário YAML — testes e demos sem gastar tokens.

Ativado por `NX_FAKE_SCENARIO=<arquivo>`: substitui as ferramentas listadas no cenário.
O consumo dos passos é persistido em `$NX_HOME/fake-state.json` para sobreviver a interrupções/retomadas.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from nexo.kernel.models.agent import (
    CLARIFICATION,
    DONE,
    ERR_TECHNICAL,
    ERROR,
    FILE_CHANGE,
    INIT,
    MESSAGE,
    PROGRESS,
    QUOTA,
    RATE_LIMITED,
    TOOL_CALL,
    AgentEvent,
    AgentOutcome,
    AgentRequest,
    AuthStatus,
    Capabilities,
    ToolInfo,
    UsageRecord,
)
from nexo.kernel.runtime import yamlfile

VENDORS = {"claude": "anthropic", "copilot": "github", "devin": "cognition"}
_LOCK = threading.Lock()


class Scenario:
    def __init__(self, path: Path, state_file: Path) -> None:
        self.path = path
        self.data = yamlfile.load(path)
        self.state_file = state_file
        self.key = f"{path}:{hashlib.sha256(path.read_bytes()).hexdigest()[:16]}"

    def tools(self) -> dict[str, dict[str, Any]]:
        return {str(k): dict(v or {}) for k, v in (self.data.get("tools") or {}).items()}

    def _state(self) -> dict[str, Any]:
        if self.state_file.is_file():
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            if data.get("scenario") == self.key:
                return dict(data)
        return {"scenario": self.key, "consumed": []}

    def next_step(self, tool: str, task: str = "", role: str = "") -> dict[str, Any] | None:
        with _LOCK:
            return self._next_step(tool, task, role)

    def _next_step(self, tool: str, task: str, role: str) -> dict[str, Any] | None:
        state = self._state()
        consumed: list[int] = list(state["consumed"])
        for index, step in enumerate(self.data.get("steps") or []):
            if index in consumed or step.get("tool") != tool:
                continue
            if step.get("task") and step.get("task") != task:
                continue
            if step.get("role") and step.get("role") != role:
                continue
            consumed.append(index)
            state["consumed"] = consumed
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(state), encoding="utf-8")
            return dict(step)
        return None


class FakeSession:
    def __init__(self, scenario: Scenario, tool: str, req: AgentRequest) -> None:
        self.scenario = scenario
        self.tool = tool
        self.req = req
        self._terminated = False

    def terminate(self) -> None:
        self._terminated = True

    def events(self) -> Iterator[AgentEvent]:
        step = self.scenario.next_step(self.tool, self.req.env.get("NX_TASK_ID", ""), self.req.role)
        if step is None:
            outcome = AgentOutcome("error", ERR_TECHNICAL, f"cenário sem passos restantes para {self.tool}")
            yield AgentEvent(DONE, {"outcome": outcome})
            return
        expected = step.get("expect_prompt")  # testes: o prompt precisa conter este trecho
        if expected and str(expected) not in self.req.prompt:
            outcome = AgentOutcome("error", ERR_TECHNICAL, f"prompt sem o trecho esperado: {expected}")
            yield AgentEvent(DONE, {"outcome": outcome})
            return
        outcome = AgentOutcome("success")
        workdir = Path(self.req.workdir)
        if not step.get("no_init"):  # como as CLIs reais; `no_init` simula mudança de formato
            yield AgentEvent(INIT, {"model": self.req.model, "tool": self.tool})
        for raw in step.get("events") or []:
            if self._terminated:
                yield AgentEvent(DONE, {"outcome": AgentOutcome("interrupted", None, "interrompido pelo Nexo")})
                return
            ((kind, data),) = raw.items() if isinstance(raw, dict) else ((raw, {}),)
            data = data or {}
            if kind == "edit":
                target = workdir / str(data["path"])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(data.get("content", "")), encoding="utf-8")
                outcome.files_changed.append(str(data["path"]))
                outcome.tool_calls += 1
                yield AgentEvent(TOOL_CALL, {"name": "Write", "input": {"file_path": str(target)}})
                yield AgentEvent(FILE_CHANGE, {"path": str(data["path"])})
            elif kind == "delete":
                (workdir / str(data["path"])).unlink(missing_ok=True)
                outcome.files_changed.append(str(data["path"]))
            elif kind == "tool":
                outcome.tool_calls += 1
                yield AgentEvent(TOOL_CALL, {"name": str(data["name"]), "input": dict(data.get("input") or {})})
            elif kind == "message":
                outcome.text += str(data.get("text", ""))
                yield AgentEvent(MESSAGE, {"text": str(data.get("text", ""))})
            elif kind == "clarification":
                yield AgentEvent(CLARIFICATION, dict(data))
            elif kind == "progress":
                yield AgentEvent(PROGRESS, dict(data))
            elif kind == "usage":
                for unit, qty in data.items():
                    if unit == "api_equiv_usd":
                        outcome.usage.append(UsageRecord("tokens_out", 0.0, api_equiv_usd=float(qty), estimated=True))
                    else:
                        outcome.usage.append(UsageRecord(str(unit), float(qty), estimated=True))
            elif kind == "quota":
                outcome.quota_utilization = float(data.get("utilization", 0))
                yield AgentEvent(QUOTA, dict(data))
            elif kind == "sleep":
                end = time.monotonic() + float(data.get("seconds", 1))
                while time.monotonic() < end and not self._terminated:
                    time.sleep(0.05)
            elif kind == "rate_limited":
                reset_at = time.time() + float(data.get("reset_in", 3600))
                yield AgentEvent(RATE_LIMITED, {"reset_at": reset_at})
                outcome.status, outcome.message, outcome.reset_at = "rate_limited", "cota esgotada", reset_at
                yield AgentEvent(DONE, {"outcome": outcome})
                return
            elif kind == "error":
                outcome.status = "error"
                outcome.error_kind = str(data.get("kind", ERR_TECHNICAL))
                outcome.message = str(data.get("message", "erro simulado"))
                yield AgentEvent(ERROR, {"kind": outcome.error_kind, "message": outcome.message})
                yield AgentEvent(DONE, {"outcome": outcome})
                return
            elif kind == "done":
                outcome.text += str(data.get("text", ""))
                structured = data.get("structured")
                outcome.structured = dict(structured) if isinstance(structured, dict) else None
        yield AgentEvent(DONE, {"outcome": outcome})


class FakeAdapter:
    def __init__(self, name: str, scenario: Scenario) -> None:
        self.name = name
        self.vendor = VENDORS.get(name, "fake")
        self.scenario = scenario
        self.spec = scenario.tools().get(name, {})

    def detect(self) -> ToolInfo:
        installed = bool(self.spec.get("installed", True))
        return ToolInfo(self.name, installed, "fake", f"fake://{self.name}" if installed else None)

    def auth_status(self) -> AuthStatus:
        return AuthStatus(bool(self.spec.get("authenticated", True)), "fake")

    def capabilities(self) -> Capabilities:
        return Capabilities(verified=True, structured_output=True, native_hooks=False, guard_proven=True, mcp=False)

    def start(self, req: AgentRequest, profile: str = "code-writer") -> FakeSession:
        return FakeSession(self.scenario, self.name, req)

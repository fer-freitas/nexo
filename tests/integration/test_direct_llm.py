"""conexão direta com as LLMs: agente pela API, política antes de executar, troca de provedor e escolha."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from nexo.kernel.models.agent import AuthStatus, ToolInfo
from nexo.kernel.runtime import http
from nexo.kernel.security.policy import PROFILES
from nexo.modules.execution.adapters.agents.claude import TOOLS_BY_PROFILE
from nexo.modules.execution.app.agents import ToolState, _apply_api_mode
from tests.conftest import Runner

VERSION_PY = "def version() -> str:\n    return '0.1.0'\n"


def a_tool(name: str, args: dict[str, Any], i: int) -> dict[str, Any]:
    return {"type": "tool_use", "id": f"tu{i}", "name": name, "input": args}


def a_msg(*blocks: dict[str, Any], stop: str = "tool_use") -> dict[str, Any]:
    return {
        "content": list(blocks),
        "stop_reason": stop,
        "usage": {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 4000},
    }


def o_msg(calls: list[tuple[str, dict[str, Any]]], text: str = "") -> dict[str, Any]:
    tc = [
        {"id": f"c{i}", "type": "function", "function": {"name": n, "arguments": json.dumps(a)}}
        for i, (n, a) in enumerate(calls)
    ]
    return {
        "choices": [
            {"message": {"content": text, "tool_calls": tc or None}, "finish_reason": "tool_calls" if tc else "stop"}
        ],
        "usage": {"prompt_tokens": 900, "completion_tokens": 100, "prompt_tokens_details": {"cached_tokens": 300}},
    }


@dataclass
class FakeLLM:
    anthropic: list[http.Response] = field(default_factory=list)
    openai: list[http.Response] = field(default_factory=list)
    calls: list[tuple[str, dict[str, str], dict[str, Any]]] = field(default_factory=list)

    def __call__(self, method: str, url: str, headers: dict[str, str], data: bytes | None) -> http.Response:
        body = json.loads(data or b"{}")
        self.calls.append((url, headers, body))
        queue = self.anthropic if "anthropic.com" in url else self.openai
        return queue.pop(0) if queue else http.Response(500, {"error": {"message": "sem resposta simulada"}})


@pytest.fixture
def llm(memory_keyring) -> Iterator[FakeLLM]:  # type: ignore[no-untyped-def]
    fake = FakeLLM()
    memory_keyring.store[("nexo-cli", "anthropic")] = "sk-ant-teste"
    memory_keyring.store[("nexo-cli", "openai")] = "sk-openai-teste"
    http.set_transport(fake)
    yield fake
    http.set_transport(None)


def enable(nexo: Runner, mode: str = "only", *providers: str) -> None:
    nexo("config", "set", "agents.api.mode", mode, "--project")
    for p in providers or ("anthropic",):
        nexo("config", "set", f"agents.api.providers.{p}.enabled", "true", "--project")


def test_task_done_through_the_api_with_cache_and_cost(project: Path, nexo: Runner, llm: FakeLLM) -> None:
    enable(nexo)
    llm.anthropic += [
        http.Response(200, a_msg(a_tool("read_file", {"path": "src/svc/__init__.py"}, 1))),
        http.Response(200, a_msg(a_tool("write_file", {"path": "src/svc/version.py", "content": VERSION_PY}, 2))),
        http.Response(200, a_msg(a_tool("report_progress", {"percent": 100, "done": ["version()"]}, 3))),
        http.Response(200, a_msg({"type": "text", "text": "Pronto."}, stop="end_turn")),
    ]
    code, out, err = nexo("--json", "run", "task", "T-1")
    rep = json.loads(out)
    assert code == 0 and rep["state"] == "Concluido", err or out
    assert rep["routes"][0].startswith("anthropic/")
    url, headers, body = llm.calls[0]
    assert url == "https://api.anthropic.com/v1/messages" and headers["x-api-key"] == "sk-ant-teste"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}  # parte fixa em cache
    assert {t["name"] for t in body["tools"]} >= {"read_file", "write_file", "run_command", "report_progress"}
    second = llm.calls[1][2]["messages"]
    assert second[-1]["content"][0]["type"] == "tool_result" and "1\t" in second[-1]["content"][0]["content"]
    cost = json.loads(nexo("--json", "cost", "--story", "T-1")[1])["rows"][0]
    assert cost["units"]["anthropic:tokens_in"] == 4000 and cost["units"]["anthropic:cache_read"] == 16000
    assert cost["by_tool"]["anthropic"] > 0


def test_policy_is_checked_before_running(project: Path, nexo: Runner, llm: FakeLLM) -> None:
    enable(nexo)
    marker = project / "nao-deveria-existir"
    llm.anthropic += [
        http.Response(200, a_msg(a_tool("run_command", {"command": f"sudo touch {marker}"}, 1))),
        http.Response(200, a_msg(a_tool("read_file", {"path": "../../../etc/hosts"}, 2))),
        http.Response(200, a_msg(a_tool("write_file", {"path": "src/svc/version.py", "content": VERSION_PY}, 3))),
        http.Response(200, a_msg(a_tool("report_progress", {"percent": 100}, 4))),
        http.Response(200, a_msg({"type": "text", "text": "ok"}, stop="end_turn")),
    ]
    code, out, _ = nexo("--json", "run", "task", "T-1")
    assert code == 0 and json.loads(out)["state"] == "Concluido"
    assert not marker.exists()  # negado antes de executar
    denied = llm.calls[1][2]["messages"][-1]["content"][0]
    assert denied["is_error"] and "NEGADO pela política" in denied["content"]
    escaped = llm.calls[2][2]["messages"][-1]["content"][0]
    assert escaped["is_error"] and ("fora do diretório" in escaped["content"] or "NEGADO" in escaped["content"])
    logs = json.loads(nexo("--json", "logs", "-n", "200")[1])
    assert any(e["type"] == "policy.violation" and e["payload"]["tool"] == "Bash" for e in logs)


def test_quota_on_one_provider_hands_off_to_the_other(project: Path, nexo: Runner, llm: FakeLLM) -> None:
    enable(nexo, "only", "anthropic", "openai")
    llm.anthropic.append(http.Response(429, {"error": {"message": "rate limit"}}, {"retry-after": "120"}))
    llm.openai += [
        http.Response(200, o_msg([("write_file", {"path": "src/svc/version.py", "content": VERSION_PY})])),
        http.Response(200, o_msg([("report_progress", {"percent": 100})])),
        http.Response(200, o_msg([], "feito")),
    ]
    code, out, err = nexo("--json", "run", "task", "T-1")
    rep = json.loads(out)
    assert code == 0 and rep["state"] == "Concluido", err or out
    assert rep["routes"][0].startswith("anthropic/") and rep["routes"][-1].startswith("openai/")
    url, headers, body = llm.calls[-1]
    assert url == "https://api.openai.com/v1/chat/completions" and headers["Authorization"] == "Bearer sk-openai-teste"
    assert body["messages"][0]["role"] == "system" and body["tools"][0]["type"] == "function"


def test_structured_answer_through_the_api(project: Path, nexo: Runner, llm: FakeLLM) -> None:
    enable(nexo, "only", "openai")
    (project / "src/svc/estorno.py").write_text("def estornar(v):\n    return -v\n", encoding="utf-8")
    answer = {
        "answer": "estornar devolve o valor negativo",
        "sources": [{"path": "src/svc/estorno.py"}],
        "confidence": "high",
    }
    llm.openai += [
        http.Response(200, o_msg([("read_file", {"path": "src/svc/estorno.py"})])),
        http.Response(200, o_msg([("entregar_resultado", answer)])),
    ]
    code, out, err = nexo("--json", "ask", "como funciona o estorno?")
    data = json.loads(out)
    assert code == 0 and data["answer"] == answer["answer"] and data["sources"][0]["path"] == "src/svc/estorno.py", err
    tools = {t["function"]["name"] for t in llm.calls[0][2]["tools"]}
    assert "entregar_resultado" in tools and "write_file" not in tools  # papel só de leitura


def test_mode_off_by_default_keeps_only_clis(project: Path, nexo: Runner, llm: FakeLLM) -> None:
    nexo("config", "set", "agents.api.providers.anthropic.enabled", "true", "--project")
    status = json.loads(nexo("--json", "doctor")[1])
    assert "anthropic" not in json.dumps(status.get("tools", status))
    assert llm.calls == []


def test_fallback_mode_and_profiles() -> None:
    def st(name: str, installed: bool, api: bool = False, limited: float | None = None) -> ToolState:
        return ToolState(ToolInfo(name, installed, "api" if api else "1.0"), AuthStatus(True), limited, None)

    states = {"claude": st("claude", True), "anthropic": st("anthropic", True, api=True)}
    assert not _apply_api_mode("fallback", dict(states))["anthropic"].info.installed  # CLI pronta: API na reserva
    limited = {**states, "claude": st("claude", True, limited=9e9)}
    assert _apply_api_mode("fallback", limited)["anthropic"].info.installed  # CLI sem cota: API entra
    assert not _apply_api_mode("only", dict(states))["claude"].info.installed
    assert set(PROFILES) <= set(TOOLS_BY_PROFILE)  # todo perfil de política tem ferramentas no Claude Code

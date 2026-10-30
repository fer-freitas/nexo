"""LLMs nativos: Amazon Bedrock (API Converse, SigV4 ou chave de API) e Google Gemini (generateContent)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from nexo.kernel.models import llm_api
from nexo.kernel.runtime import http
from nexo.kernel.secrets.aws_sigv4 import sign
from tests.conftest import Runner

VERSION_PY = "def version() -> str:\n    return '0.1.0'\n"


def b_msg(*calls: tuple[str, dict[str, Any]], text: str = "") -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"text": text}] if text else []
    content += [{"toolUse": {"toolUseId": f"tu{i}", "name": n, "input": a}} for i, (n, a) in enumerate(calls)]
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": "tool_use" if calls else "end_turn",
        "usage": {"inputTokens": 800, "outputTokens": 100, "cacheReadInputTokens": 2000, "cacheWriteInputTokens": 0},
    }


def g_msg(*calls: tuple[str, dict[str, Any]], text: str = "") -> dict[str, Any]:
    parts: list[dict[str, Any]] = [{"text": text}] if text else []
    parts += [{"functionCall": {"name": n, "args": a}} for n, a in calls]
    return {
        "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 150, "cachedContentTokenCount": 400},
    }


@dataclass
class FakeCloud:
    bedrock: list[http.Response] = field(default_factory=list)
    gemini: list[http.Response] = field(default_factory=list)
    calls: list[tuple[str, dict[str, str], bytes]] = field(default_factory=list)

    def __call__(self, method: str, url: str, headers: dict[str, str], data: bytes | None) -> http.Response:
        self.calls.append((url, headers, data or b""))
        queue = self.bedrock if "amazonaws.com" in url else self.gemini
        return queue.pop(0) if queue else http.Response(500, {"message": "sem resposta simulada"})


@pytest.fixture
def cloud(memory_keyring, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeCloud]:  # type: ignore[no-untyped-def]
    for var in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_BEARER_TOKEN_BEDROCK"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    fake = FakeCloud()
    http.set_transport(fake)
    yield fake
    http.set_transport(None)


def enable(nexo: Runner, provider: str, **extra: str) -> None:
    nexo("config", "set", "agents.api.mode", "only", "--project")
    nexo("config", "set", "data_class", "public", "--project")  # AWS e Google exigem liberação na data_policy
    nexo("config", "set", f"agents.api.providers.{provider}.enabled", "true", "--project")
    for k, v in extra.items():
        nexo("config", "set", f"agents.api.providers.{provider}.{k}", v, "--project")


def test_sigv4_matches_aws_reference_vector() -> None:
    """Vetor `get-vanilla` da suíte oficial de testes da Signature Version 4."""
    h = sign(
        "GET",
        "https://example.amazonaws.com/",
        {},
        b"",
        "AKIDEXAMPLE",
        "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY",
        "us-east-1",
        "service",
        now=datetime(2015, 8, 30, 12, 36, tzinfo=UTC),
    )
    assert h["Authorization"].endswith("Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31")


def test_task_done_on_bedrock_with_sigv4(
    project: Path, nexo: Runner, cloud: FakeCloud, monkeypatch: pytest.MonkeyPatch
) -> None:
    enable(nexo, "bedrock", region="sa-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAEXEMPLO")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "segredo/exemplo")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "token-da-sessao")
    cloud.bedrock += [
        http.Response(200, b_msg(("read_file", {"path": "src/svc/__init__.py"}))),
        http.Response(200, b_msg(("write_file", {"path": "src/svc/version.py", "content": VERSION_PY}))),
        http.Response(200, b_msg(("report_progress", {"percent": 100}))),
        http.Response(200, b_msg(text="Pronto.")),
    ]
    code, out, err = nexo("--json", "run", "task", "T-1")
    rep = json.loads(out)
    assert code == 0 and rep["state"] == "Concluido", err or out
    assert rep["routes"][0].startswith("bedrock/")
    url, headers, data = cloud.calls[0]
    assert url.startswith("https://bedrock-runtime.sa-east-1.amazonaws.com/model/us.anthropic.claude-")
    assert url.endswith("%3A0/converse")
    auth = headers["Authorization"]
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIAEXEMPLO/") and "/sa-east-1/bedrock/aws4_request" in auth
    assert headers["x-amz-security-token"] == "token-da-sessao"
    body = json.loads(data)
    assert body["system"][1] == {"cachePoint": {"type": "default"}}
    assert {t["toolSpec"]["name"] for t in body["toolConfig"]["tools"]} >= {"read_file", "write_file"}
    second = json.loads(cloud.calls[1][2])["messages"]
    assert second[-1]["content"][0]["toolResult"]["toolUseId"] == "tu0"
    assert (project / ".nexo").exists()
    cost = json.loads(nexo("--json", "cost", "--story", "T-1")[1])["rows"][0]
    assert cost["by_tool"]["bedrock"] > 0  # preço do Claude pelo nome sem o prefixo do Bedrock


def test_bedrock_api_key_and_model_override(project: Path, nexo: Runner, cloud: FakeCloud, memory_keyring) -> None:  # type: ignore[no-untyped-def]
    enable(nexo, "bedrock")
    nexo("config", "set", "agents.api.providers.bedrock.models.claude-sonnet", "meu-perfil-sonnet", "--project")
    nexo("config", "set", "agents.api.providers.bedrock.prompt_cache", "false", "--project")
    memory_keyring.store[("nexo-cli", "bedrock")] = "chave-api-bedrock"
    cloud.bedrock += [
        http.Response(200, b_msg(("write_file", {"path": "src/svc/version.py", "content": VERSION_PY}))),
        http.Response(200, b_msg(("report_progress", {"percent": 100}))),
        http.Response(200, b_msg(text="ok")),
    ]
    code, out, err = nexo("--json", "--tool", "bedrock", "--model", "claude-sonnet", "run", "task", "T-1")
    assert code == 0 and json.loads(out)["state"] == "Concluido", err or out
    url, headers, data = cloud.calls[0]
    assert url == "https://bedrock-runtime.us-east-1.amazonaws.com/model/meu-perfil-sonnet/converse"
    assert headers["Authorization"] == "Bearer chave-api-bedrock"
    assert len(json.loads(data)["system"]) == 1  # sem cachePoint


def test_bedrock_throttling_hands_off_to_gemini(project: Path, nexo: Runner, cloud: FakeCloud, memory_keyring) -> None:  # type: ignore[no-untyped-def]
    enable(nexo, "bedrock")
    enable(nexo, "gemini-api")
    memory_keyring.store[("nexo-cli", "bedrock")] = "chave-api-bedrock"
    memory_keyring.store[("nexo-cli", "gemini_api")] = "chave-gemini"
    cloud.bedrock.append(http.Response(429, {"message": "Too many requests"}, {"Retry-After": "90"}))
    cloud.gemini += [
        http.Response(200, g_msg(("write_file", {"path": "src/svc/version.py", "content": VERSION_PY}))),
        http.Response(200, g_msg(("report_progress", {"percent": 100}))),
        http.Response(200, g_msg(text="feito")),
    ]
    code, out, err = nexo("--json", "run", "task", "T-1")
    rep = json.loads(out)
    assert code == 0 and rep["state"] == "Concluido", err or out
    assert any(r.startswith("bedrock/") for r in rep["routes"]) and rep["routes"][-1].startswith("gemini-api/")
    url, headers, data = cloud.calls[-1]
    assert url.startswith("https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-")
    assert url.endswith(":generateContent") and headers["x-goog-api-key"] == "chave-gemini"
    body = json.loads(data)
    assert body["systemInstruction"]["parts"][0]["text"]
    names = {f["name"] for f in body["tools"][0]["functionDeclarations"]}
    assert {"read_file", "run_command"} <= names
    last = body["contents"][-1]["parts"][0]["functionResponse"]
    assert last["name"] == "report_progress" and "result" in last["response"]


def test_gemini_invalid_key_is_auth_error(project: Path, nexo: Runner, cloud: FakeCloud, memory_keyring) -> None:  # type: ignore[no-untyped-def]
    enable(nexo, "gemini-api")
    memory_keyring.store[("nexo-cli", "gemini_api")] = "chave-errada"
    cloud.gemini.append(
        http.Response(400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key."}})
    )
    rep = json.loads(nexo("--json", "run", "task", "T-1")[1])
    assert rep["state"] != "Concluido"
    logs = json.dumps(json.loads(nexo("--json", "logs", "-n", "200")[1]))
    assert "auth" in logs


def test_gemini_schema_is_reduced_to_what_the_api_accepts() -> None:
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "answer": {"type": ["string", "null"], "title": "x"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["answer"],
    }
    out = llm_api.gemini_schema(schema)
    assert out == {
        "type": "object",
        "properties": {
            "answer": {"type": "string", "nullable": True},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["answer"],
    }


def test_new_cloud_vendors_need_opt_in(project: Path, nexo: Runner, cloud: FakeCloud, memory_keyring) -> None:  # type: ignore[no-untyped-def]
    nexo("config", "set", "agents.api.mode", "only", "--project")
    nexo("config", "set", "agents.api.providers.bedrock.enabled", "true", "--project")
    memory_keyring.store[("nexo-cli", "bedrock")] = "chave-api-bedrock"
    rep = json.loads(nexo("--json", "run", "task", "T-1")[1])
    assert rep["state"] != "Concluido" and not cloud.calls  # dado confidencial não sai sem a org liberar a AWS


def test_price_name_strips_bedrock_prefixes() -> None:
    assert llm_api.price_name("us.anthropic.claude-sonnet-4-5-20250929-v1:0").startswith("claude-sonnet")
    assert llm_api.price_name("anthropic.claude-haiku-4-5") == "claude-haiku-4-5"
    assert llm_api.price_name("gemini-2.5-pro") == "gemini-2.5-pro"

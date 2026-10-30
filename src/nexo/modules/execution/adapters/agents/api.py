"""Agente direto na API das LLMs: o Nexo conversa com o modelo e executa as ferramentas ele mesmo.

Diferente das CLIs (que executam e só depois avisam), aqui toda ferramenta passa pela política **antes** de rodar;
o que for negado volta para o modelo com o motivo e fica na trilha de auditoria.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexo.kernel.errors import NexoError
from nexo.kernel.models import llm_api
from nexo.kernel.models.agent import (
    DONE,
    ERR_AUTH,
    ERR_CONTEXT,
    ERR_OVERLOADED,
    ERR_TECHNICAL,
    ERR_TIMEOUT,
    FILE_CHANGE,
    MESSAGE,
    TOOL_CALL,
    AgentEvent,
    AgentOutcome,
    AgentRequest,
    AuthStatus,
    Capabilities,
    ToolInfo,
    UsageRecord,
)
from nexo.kernel.runtime import http
from nexo.kernel.security.environment import filter_env
from nexo.kernel.security.policy import Policy, evaluate, normalize

MAX_READ = 200_000
MAX_OUTPUT = 4_000
COMMAND_TIMEOUT = 300
ALL_STATUS = tuple(range(200, 600))
VENDOR = {
    "anthropic": "anthropic",
    "openai": "openai",
    "azure-openai": "microsoft",
    "gateway": "gateway",
    "bedrock": "aws",
    "gemini-api": "google",
}
# apelido da rota → id do modelo no Bedrock (perfil de inferência); `providers.bedrock.models` sobrescreve
BEDROCK_MODELS = {
    "claude-haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "claude-sonnet": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "claude-opus": "us.anthropic.claude-opus-4-1-20250805-v1:0",
}


@dataclass(frozen=True)
class Style:
    """Formato de uma API: primeira mensagem, pedido, resposta, turno do modelo e resultados das ferramentas."""

    first: Callable[[str], dict[str, Any]]
    request: Callable[..., dict[str, Any]]
    reply: Callable[[dict[str, Any]], llm_api.Reply]
    assistant: Callable[[dict[str, Any]], dict[str, Any]]
    results: Callable[[list[tuple[llm_api.ToolCall, str, bool]]], list[dict[str, Any]]]


def _plain_first(prompt: str) -> dict[str, Any]:
    return {"role": "user", "content": prompt}


STYLES = {
    "anthropic": Style(
        _plain_first,
        llm_api.anthropic_request,
        llm_api.anthropic_reply,
        llm_api.anthropic_assistant,
        llm_api.anthropic_results,
    ),
    "openai": Style(
        _plain_first, llm_api.openai_request, llm_api.openai_reply, llm_api.openai_assistant, llm_api.openai_results
    ),
    "bedrock": Style(
        llm_api.bedrock_first,
        llm_api.bedrock_request,
        llm_api.bedrock_reply,
        llm_api.bedrock_assistant,
        llm_api.bedrock_results,
    ),
    "gemini": Style(
        llm_api.gemini_first,
        llm_api.gemini_request,
        llm_api.gemini_reply,
        llm_api.gemini_assistant,
        llm_api.gemini_results,
    ),
}


def bedrock_headers(key: str, url: str, data: bytes, region: str) -> dict[str, str]:
    """Credencial `ACCESS_KEY:SECRET[:SESSION_TOKEN]` → SigV4; sem `:` → chave de API do Bedrock (Bearer)."""
    parts = key.split(":")
    if len(parts) in (2, 3) and all(parts):
        from nexo.kernel.secrets.aws_sigv4 import sign

        base = {"content-type": "application/json", "x-amz-content-sha256": hashlib.sha256(data).hexdigest()}
        return sign(
            "POST", url, base, data, parts[0], parts[1], region, "bedrock", parts[2] if len(parts) == 3 else None
        )
    return {"Authorization": f"Bearer {key}"}


@dataclass
class Provider:
    """Como falar com um provedor: URL, cabeçalhos e formato (anthropic | openai)."""

    name: str
    style: str
    url: Callable[[str], str]
    headers: Callable[[str], dict[str, str]]
    max_tokens: int = 8192
    signer: Callable[[str, str, bytes], dict[str, str]] | None = None  # cabeçalhos que dependem do corpo (SigV4)

    @staticmethod
    def build(name: str, cfg: dict[str, Any]) -> Provider:
        max_tokens = int(cfg.get("max_tokens") or 8192)
        if name == "anthropic":
            base = str(cfg.get("base_url") or "https://api.anthropic.com").rstrip("/")
            return Provider(
                name,
                "anthropic",
                lambda _m: f"{base}/v1/messages",
                lambda key: {"x-api-key": key, "anthropic-version": "2023-06-01"},
                max_tokens,
            )
        if name == "azure-openai":
            endpoint = str(cfg.get("endpoint") or "").rstrip("/")
            version = str(cfg.get("api_version") or "2024-10-21")
            deployments = dict(cfg.get("deployments") or {})
            return Provider(
                name,
                "openai",
                lambda m: (
                    f"{endpoint}/openai/deployments/{deployments.get(m, m)}/chat/completions?api-version={version}"
                ),
                lambda key: {"api-key": key},
                max_tokens,
            )
        if name == "bedrock":
            region = str(cfg.get("region") or "us-east-1")
            root = str(cfg.get("base_url") or f"https://bedrock-runtime.{region}.amazonaws.com").rstrip("/")
            return Provider(
                name,
                "bedrock",
                lambda m: f"{root}/model/{urllib.parse.quote(m, safe='')}/converse",
                lambda key: {},
                max_tokens,
                lambda key, url, data: bedrock_headers(key, url, data, region),
            )
        if name == "gemini-api":
            root = str(cfg.get("base_url") or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
            return Provider(
                name,
                "gemini",
                lambda m: f"{root}/models/{urllib.parse.quote(m, safe='.-_')}:generateContent",
                lambda key: {"x-goog-api-key": key},
                max_tokens,
            )
        default = "https://api.openai.com/v1" if name == "openai" else ""
        base = str(cfg.get("base_url") or default).rstrip("/")
        return Provider(
            name,
            "openai",
            lambda _m: f"{base}/chat/completions",
            lambda key: {"Authorization": f"Bearer {key}"},
            max_tokens,
        )


@dataclass
class Hooks:
    """O que o adaptador precisa do restante do Nexo (injetado pela camada de aplicação)."""

    secret: Callable[[], str | None]
    policy: Callable[[], Policy]
    on_denied: Callable[[str, str, dict[str, Any], str], None] = lambda *_: None  # (run, ferramenta, entrada, perfil)
    search_brain: Callable[[str], str] = lambda _q: "cérebro indisponível"
    prices: list[tuple[str, float, float]] = field(default_factory=llm_api.price_table)


class ApiAdapter:
    def __init__(self, name: str, cfg: dict[str, Any], hooks: Hooks) -> None:
        self.name = name
        self.vendor = VENDOR.get(name, name)
        self.cfg = cfg
        self.hooks = hooks
        self.provider = Provider.build(name, cfg)

    def _configured(self) -> bool:
        if not self.cfg.get("enabled"):
            return False
        if self.name == "azure-openai" and not self.cfg.get("endpoint"):
            return False
        if self.name == "gateway" and not self.cfg.get("base_url"):
            return False
        return bool(self.hooks.secret())

    def detect(self) -> ToolInfo:
        return ToolInfo(
            self.name, self._configured(), "api", self.provider.url("modelo") if self._configured() else None
        )

    def auth_status(self) -> AuthStatus:
        ok = bool(self.hooks.secret())
        return AuthStatus(ok, "chave configurada" if ok else f"sem chave: nexo login {self.name.replace('-', '_')}")

    def capabilities(self) -> Capabilities:
        # native_hooks: a política é aplicada aqui dentro, antes de cada ferramenta
        return Capabilities(verified=True, structured_output=True, native_hooks=True, usage_unit="tokens")

    def start(self, req: AgentRequest, profile: str = "code-writer") -> ApiSession:
        from dataclasses import replace

        # apelido da rota → nome do modelo no provedor (gateway, Azure, Bedrock)
        models = {**(BEDROCK_MODELS if self.name == "bedrock" else {}), **dict(self.cfg.get("models") or {})}
        return ApiSession(self, replace(req, model=str(models.get(req.model, req.model))), profile)


class ApiSession:
    def __init__(self, adapter: ApiAdapter, req: AgentRequest, profile: str) -> None:
        self.a = adapter
        self.req = req
        self.profile = profile
        self.workdir = Path(req.workdir).resolve()
        self._terminated = False
        self.files: list[str] = []
        self.tool_calls = 0

    def terminate(self) -> None:
        self._terminated = True

    # ------------------------------------------------------------ laço do agente

    def events(self) -> Iterator[AgentEvent]:
        key = self.a.hooks.secret()
        if not key:
            yield self._done(AgentOutcome("error", ERR_AUTH, f"sem chave de API para {self.a.name}"))
            return
        prov = self.a.provider
        style = STYLES[prov.style]
        structured = self.req.output_schema is not None
        tools = llm_api.tools_for(self.profile == "read-only", self.req.output_schema)
        system = llm_api.system_text("", structured)
        messages: list[dict[str, Any]] = [style.first(self.req.prompt)]
        usage = {"tokens_in": 0.0, "tokens_out": 0.0, "cache_read": 0.0, "cache_write": 0.0}
        deadline = time.monotonic() + self.req.timeout_s
        text, result = "", None
        policy = self.a.hooks.policy()
        for _turn in range(max(1, self.req.max_turns)):
            if self._terminated:
                yield self._done(AgentOutcome("interrupted", None, "interrompido pelo Nexo"), usage)
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                yield self._done(AgentOutcome("timeout", ERR_TIMEOUT, "tempo limite excedido"), usage)
                return
            body = style.request(self.req.model, system, messages, tools, prov.max_tokens)
            if prov.style == "bedrock" and self.a.cfg.get("prompt_cache") is False:
                body["system"] = body["system"][:1]  # modelo sem cache de prompt no Bedrock
            url = prov.url(self.req.model)
            signed = prov.signer(key, url, json.dumps(body).encode("utf-8")) if prov.signer else prov.headers(key)
            try:
                resp = http.request(
                    "POST",
                    url,
                    {**signed, "Content-Type": "application/json"},
                    body,
                    expect=ALL_STATUS,
                    timeout=min(remaining, 600),
                )
            except NexoError as exc:
                yield self._done(AgentOutcome("error", ERR_TECHNICAL, str(exc)[:500]), usage)
                return
            if not resp.ok:
                yield self._done(_http_outcome(resp), usage)
                return
            reply = style.reply(resp.body)
            for k in usage:
                usage[k] += reply.usage.get(k, 0.0)
            if reply.text:
                text = reply.text
                yield AgentEvent(MESSAGE, {"text": reply.text[:2000]})
            messages.append(style.assistant(resp.body))
            if not reply.calls:
                break
            results: list[tuple[llm_api.ToolCall, str, bool]] = []
            for call in reply.calls:
                if call.name == llm_api.FINAL_TOOL:
                    result = call.args
                    results.append((call, "resultado recebido", False))
                    continue
                out, err = yield from self._tool(call, policy)
                results.append((call, out, err))
            messages += style.results(results)
            if result is not None:
                break
        else:
            yield self._done(AgentOutcome("error", ERR_TECHNICAL, f"limite de {self.req.max_turns} turnos"), usage)
            return
        if structured and result is None:
            result = _json_in(text)
            if result is None:
                yield self._done(AgentOutcome("error", ERR_TECHNICAL, "o modelo não entregou o resultado"), usage)
                return
        outcome = AgentOutcome("success", None, "", text=text, structured=result)
        yield self._done(outcome, usage)

    def _done(self, outcome: AgentOutcome, usage: dict[str, float] | None = None) -> AgentEvent:
        if usage:
            cost = llm_api.usd(self.req.model, usage, self.a.hooks.prices)
            records = [
                UsageRecord("tokens_in", usage["tokens_in"]),
                UsageRecord("tokens_out", usage["tokens_out"], api_equiv_usd=cost),
                UsageRecord("cache_read", usage["cache_read"]),
                UsageRecord("cache_write", usage["cache_write"]),
            ]
            outcome.usage = [r for r in records if r.quantity or r.api_equiv_usd]
        outcome.files_changed = sorted(set(self.files))
        outcome.tool_calls = self.tool_calls
        return AgentEvent(DONE, {"outcome": outcome})

    # ------------------------------------------------------------ ferramentas

    def _tool(self, call: llm_api.ToolCall, policy: Policy) -> Generator[AgentEvent, None, tuple[str, bool]]:
        """Executa uma ferramenta (gerador: emite eventos e devolve (texto, erro?))."""
        self.tool_calls += 1
        name, args = call.name, call.args
        native = llm_api.POLICY_NAME.get(name)
        if native is not None:
            pin = llm_api.policy_input(name, args)
            yield AgentEvent(TOOL_CALL, {"name": native, "input": pin})
            decision = evaluate(normalize(native, pin, policy), str(self.workdir), policy, self.profile)
            if not decision.allow:
                self.a.hooks.on_denied(self.req.env.get("NX_RUN_ID", ""), native, pin, self.profile)
                return f"NEGADO pela política da empresa ({decision.rule}): {decision.reason}", True
        try:
            if name == "read_file":
                return self._read(args), False
            if name == "list_files":
                return "\n".join(self._list(str(args.get("pattern") or ""))[:500]) or "(nenhum arquivo)", False
            if name == "search":
                return self._search(str(args.get("pattern") or ""), str(args.get("glob") or "")), False
            if name in ("write_file", "edit_file"):
                rel = self._write(name, args)
                yield AgentEvent(FILE_CHANGE, {"path": rel})
                return f"ok: {rel}", False
            if name == "run_command":
                return self._run(str(args.get("command") or "")), False
            if name == "report_progress":
                from nexo.kernel.models.agent import PROGRESS

                yield AgentEvent(PROGRESS, {k: args[k] for k in args if k in _PROGRESS_KEYS})
                return "progresso registrado", False
            if name == "request_clarification":
                from nexo.kernel.models.agent import CLARIFICATION

                yield AgentEvent(
                    CLARIFICATION,
                    {
                        "question": str(args.get("question", "")),
                        "section": str(args.get("section", "")),
                        "blocking": bool(args.get("blocking", True)),
                    },
                )
                return "dúvida registrada; se bloqueante, encerre e aguarde a resposta", False
            if name == "search_brain":
                return self.a.hooks.search_brain(str(args.get("query") or ""))[:MAX_OUTPUT], False
        except (OSError, ValueError, re.error, subprocess.SubprocessError) as exc:
            return f"erro: {exc}", True
        return f"ferramenta desconhecida: {name}", True

    def _path(self, rel: str) -> Path:
        path = (self.workdir / rel).resolve()
        if path != self.workdir and self.workdir not in path.parents:
            raise ValueError(f"caminho fora do diretório de trabalho: {rel}")
        return path

    def _read(self, args: dict[str, Any]) -> str:
        path = self._path(str(args.get("path", "")))
        if path.stat().st_size > MAX_READ * 5:
            raise ValueError("arquivo grande demais; use offset e limit")
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(1, int(args.get("offset") or 1))
        count = int(args.get("limit") or 2000)
        chunk = lines[start - 1 : start - 1 + count]
        out = "\n".join(f"{i}\t{ln}" for i, ln in enumerate(chunk, start))
        return out[:MAX_READ] + (f"\n[… {len(lines)} linhas no total]" if len(lines) > start - 1 + count else "")

    def _list(self, pattern: str) -> list[str]:
        proc = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=self.workdir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        files = [f for f in proc.stdout.splitlines() if f] if proc.returncode == 0 else []
        if not files:
            files = [p.relative_to(self.workdir).as_posix() for p in self.workdir.rglob("*") if p.is_file()]
        return sorted(f for f in files if not pattern or fnmatch.fnmatch(f, pattern))

    def _search(self, pattern: str, glob: str) -> str:
        rx = re.compile(pattern)
        hits: list[str] = []
        for rel in self._list(glob):
            path = self.workdir / rel
            if not path.is_file() or path.stat().st_size > 1_000_000:
                continue
            for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{rel}:{i}: {line.strip()[:200]}")
                    if len(hits) >= 100:
                        return "\n".join(hits) + "\n[… mais resultados; refine a busca]"
        return "\n".join(hits) or "(nenhuma ocorrência)"

    def _write(self, name: str, args: dict[str, Any]) -> str:
        rel = str(args.get("path", ""))
        path = self._path(rel)
        if name == "write_file":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(args.get("content", "")), encoding="utf-8")
        else:
            current = path.read_text(encoding="utf-8")
            old, new = str(args.get("old", "")), str(args.get("new", ""))
            count = current.count(old) if old else 0
            if count != 1:
                raise ValueError(f"o trecho precisa aparecer exatamente uma vez (aparece {count})")
            path.write_text(current.replace(old, new, 1), encoding="utf-8")
        rel = path.relative_to(self.workdir).as_posix()
        self.files.append(rel)
        return rel

    def _run(self, command: str) -> str:
        if not command.strip():
            raise ValueError("comando vazio")
        env, _ = filter_env(os.environ, self.req.env_allow)  # nunca herda o ambiente inteiro
        env.update(self.req.env)
        shell = ["cmd", "/c", command] if sys.platform == "win32" else ["bash", "-lc", command]
        if self.req.container:  # S-04: no modo isolado o comando roda no contêiner
            from nexo.kernel.security.isolation import container_command

            c = self.req.container
            shell = container_command(
                shell,
                str(self.workdir),
                str(c["image"]),
                [k for k in env if k not in {"PATH", "HOME", "LANG", "TMPDIR", "TEMP", "SYSTEMROOT"}],
                str(c.get("engine", "docker")),
                str(c.get("network", "nexo-egress")),
                str(c.get("proxy", "http://nexo-egress:3128")),
            )
        # Por desenho, é a ferramenta "rodar comando" do agente (como o Bash das CLIs): cada segmento (&&, |, ;) já
        # passou pela política da empresa em `_tool`, antes de chegar aqui; ambiente filtrado e prazo limitado.
        proc = subprocess.run(
            shell,  # nosemgrep — ver comentário acima (comando do agente, já conferido pela política)
            cwd=self.workdir,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=COMMAND_TIMEOUT,
            check=False,
        )
        from nexo.kernel.finops import tokens

        output = tokens.condense((proc.stdout or "") + (proc.stderr or ""), MAX_OUTPUT)  # só o essencial
        return f"código de saída {proc.returncode}\n{output}"


_PROGRESS_KEYS = ("percent", "done", "decisions", "next_steps", "blockers")


def _json_in(text: str) -> dict[str, Any] | None:
    import json

    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _http_outcome(resp: http.Response) -> AgentOutcome:
    body = resp.body if isinstance(resp.body, dict) else {}
    err = body.get("error") if isinstance(body.get("error"), dict) else body
    message = str((err or {}).get("message") or resp.body)[:500]
    if resp.status == 429:
        retry = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
        reset = time.time() + float(retry) if retry and str(retry).replace(".", "", 1).isdigit() else time.time() + 60
        return AgentOutcome("rate_limited", "rate_limit", f"cota da API esgotada: {message}", reset_at=reset)
    if resp.status in (401, 403) or re.search(r"(?i)api key not valid|API_KEY_INVALID|security token", message):
        return AgentOutcome("error", ERR_AUTH, f"chave de API recusada: {message}")
    if resp.status in (500, 502, 503, 504, 529):
        return AgentOutcome("error", ERR_OVERLOADED, f"provedor indisponível ({resp.status}): {message}")
    if re.search(r"(?i)too long|context|maximum.*tokens", message):
        return AgentOutcome("error", ERR_CONTEXT, message)
    return AgentOutcome("error", ERR_TECHNICAL, f"HTTP {resp.status}: {message}")

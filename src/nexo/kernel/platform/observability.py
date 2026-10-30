"""Observabilidade técnica da CLI: traces OTLP, transcrições redigidas por passo e relatório de
falha sanitizado.

- Traces: span por comando → execução → passo (chamadas de ferramenta como eventos) → chamada de LLM/pipeline.
  Ficam num spool local e saem em segundo plano para `observability.otlp.endpoint` (só org/squad definem o destino).
- Transcrições: eventos do agente por passo, redigidos, em `$NX_HOME/transcripts/<run>/`, retenção de 90 dias,
  leitura auditada (`nexo logs --trace <run>`).
"""

from __future__ import annotations

import contextlib
import json
import platform
import time
import traceback
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from nexo import __version__
from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.monitoring.otlp import STATUS_ERROR, Span, new_id, to_otlp
from nexo.kernel.platform import config
from nexo.kernel.runtime import http
from nexo.kernel.secrets import credentials
from nexo.kernel.security.redaction import redact

TRUSTED = ("default", "org", "squad")
SPOOL = "otlp-spool.jsonl"
MAX_SPOOL = 20_000  # spans; acima disso descarta os mais antigos (nunca cresce sem limite offline)


# ---------------------------------------------------------------- configuração (só camadas confiáveis)


def _cfg(ctx: AppContext) -> dict[str, Any]:
    from nexo.kernel.config.cascade import resolve

    layers = [layer for layer in config.load_layers(ctx) if layer.level in TRUSTED]
    return dict(resolve(layers).values.get("observability") or {})


def otlp_endpoint(ctx: AppContext) -> str:
    url = str((_cfg(ctx).get("otlp") or {}).get("endpoint") or "").rstrip("/")
    if url and not url.startswith("https://") and not url.startswith(("http://127.0.0.1", "http://localhost")):
        raise NexoError("observability.otlp.endpoint deve usar https://")
    return url


# ---------------------------------------------------------------- tracer


class Tracer:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx = ctx
        self.trace_id = new_id(16)
        self.stack: list[Span] = []
        self.done: list[Span] = []

    @contextlib.contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[Span]:
        parent = self.stack[-1].span_id if self.stack else ""
        current = Span(name, self.trace_id, new_id(8), parent)
        current.set(**attrs)
        self.stack.append(current)
        try:
            yield current
        except BaseException as exc:
            current.error(type(exc).__name__ + (f": {exc.message}" if isinstance(exc, NexoError) else ""))
            raise
        finally:
            current.end_ns = time.time_ns()
            self.stack.pop()
            self.done.append(current)

    def current(self) -> Span | None:
        return self.stack[-1] if self.stack else None


def tracer(ctx: AppContext) -> Tracer:
    existing = ctx.__dict__.get("_tracer")
    if existing is None:
        existing = Tracer(ctx)
        ctx.__dict__["_tracer"] = existing
    return existing


def span(ctx: AppContext, name: str, **attrs: Any) -> contextlib.AbstractContextManager[Span]:
    return tracer(ctx).span(name, **attrs)


def event(ctx: AppContext, name: str, **attrs: Any) -> None:
    current = tracer(ctx).current()
    if current is not None:
        current.event(name, **attrs)


def spool(ctx: AppContext) -> int:
    """Fim do comando: spans concluídos vão para o spool (só se houver destino configurado)."""
    t = ctx.__dict__.get("_tracer")
    if t is None or not t.done:
        return 0
    from nexo.kernel.platform import flags

    if not flags.enabled(ctx, "traces"):
        t.done = []
        return 0
    try:
        if not otlp_endpoint(ctx):
            return 0
    except NexoError:
        return 0
    path = ctx.home / SPOOL
    with path.open("a", encoding="utf-8") as fh:
        for s in t.done:
            fh.write(json.dumps(s.as_dict(), ensure_ascii=False) + "\n")
    count = len(t.done)
    t.done = []
    return count


def pending(ctx: AppContext) -> int:
    path = ctx.home / SPOOL
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8") as fh:
        return sum(1 for _ in fh)


def export(ctx: AppContext, batch: int = 500) -> dict[str, Any]:
    """Envia o spool ao coletor OTLP da empresa (APM). Falha → o spool fica para a próxima tentativa."""
    url = otlp_endpoint(ctx)
    path = ctx.home / SPOOL
    if not url or not path.is_file():
        return {"exported": 0, "pending": pending(ctx)}
    lines = path.read_text(encoding="utf-8").splitlines()[-MAX_SPOOL:]
    spans = [Span.from_dict(json.loads(line)) for line in lines if line.strip()]
    headers = dict(json.loads(credentials.get_secret("otlp_headers") or "{}"))
    service = str((_cfg(ctx).get("otlp") or {}).get("service_name") or "nexo-cli")
    resource = {"service.name": service, "service.version": __version__, "os.type": platform.system().lower()}
    sent = 0
    try:
        for start in range(0, len(spans), batch):
            http.request("POST", f"{url}/v1/traces", headers, to_otlp(spans[start : start + batch], resource))
            sent += len(spans[start : start + batch])
    except (NexoError, OSError) as exc:
        remaining = lines[sent:]
        path.write_text("".join(f"{line}\n" for line in remaining), encoding="utf-8")
        return {"exported": sent, "pending": len(remaining), "error": str(exc)[:200]}
    path.unlink(missing_ok=True)
    return {"exported": sent, "pending": 0}


# ---------------------------------------------------------------- transcrições

RETENTION_DAYS = 90


def transcript_path(ctx: AppContext, run_id: str, step_id: str) -> Path:
    return ctx.home / "transcripts" / run_id / f"{step_id}.jsonl"


def record(ctx: AppContext, run_id: str | None, step_id: str | None, kind: str, data: dict[str, Any]) -> None:
    """Evento do agente redigido na transcrição do passo (nada de segredo; conteúdo de arquivo não é gravado)."""
    if not run_id or not step_id:
        return
    cached = ctx.__dict__.get("_transcripts_on")
    if cached is None:
        from nexo.kernel.platform import flags

        cached = flags.enabled(ctx, "transcripts")
        ctx.__dict__["_transcripts_on"] = cached
    if not cached:
        return
    keep: dict[str, Any] = {}
    outcome = data.get("outcome")
    if outcome is not None and hasattr(outcome, "status"):  # evento final: só o resultado, não o texto
        keep.update({"status": outcome.status, "error": outcome.error_kind, "tool_calls": outcome.tool_calls})
    for key in ("name", "path", "status", "kind", "id", "error", "percent", "text", "message", "reason"):
        if key in data and data[key] is not None:
            value = data[key]
            keep[key] = redact(str(value))[:2000] if isinstance(value, str) else value
    if "input" in data and isinstance(data["input"], dict):  # só os nomes dos parâmetros, e o comando redigido
        tool_input = dict(data["input"])
        keep["input"] = {
            k: (redact(str(v))[:500] if k in ("command", "file_path", "pattern") else "…")
            for k, v in tool_input.items()
        }
    path = transcript_path(ctx, run_id, step_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": time.time(), "kind": kind, **keep}, ensure_ascii=False) + "\n")


def purge_transcripts(ctx: AppContext) -> int:
    root = ctx.home / "transcripts"
    if not root.is_dir():
        return 0
    limit = time.time() - RETENTION_DAYS * 86400
    removed = 0
    for run_dir in root.iterdir():
        if run_dir.is_dir() and run_dir.stat().st_mtime < limit:
            for f in run_dir.iterdir():
                f.unlink(missing_ok=True)
            run_dir.rmdir()
            removed += 1
    return removed


def trace(ctx: AppContext, run_id: str, reason: str) -> list[dict[str, Any]]:
    """Linha do tempo redigida de uma execução; toda leitura é auditada com motivo."""
    if len(reason.strip()) < 10:
        raise NexoError("ler a transcrição exige --reason (auditado)")
    root = ctx.home / "transcripts" / run_id
    if not root.is_dir():
        raise NexoError(f"sem transcrição para {run_id} (retenção de {RETENTION_DAYS} dias)")
    steps = {r["id"]: dict(r) for r in ctx.conn.execute("SELECT * FROM steps WHERE run_id = ? ORDER BY seq", (run_id,))}
    out = []
    for step_id, row in steps.items():
        path = transcript_path(ctx, run_id, step_id)
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.is_file() else []
        out.append(
            {
                "step": step_id,
                "role": row["role"],
                "tool": row["tool"],
                "model": row["model"],
                "outcome": row["outcome"],
                "events": events,
            }
        )
    ctx.audit("transcript.viewed", {"run": run_id, "reason": reason})
    return out


# ---------------------------------------------------------------- relatório de falha


def crash_report(ctx: AppContext, exc: BaseException, command: str) -> dict[str, Any]:
    """Pilha sanitizada: só módulo:função:linha do próprio Nexo; mensagem redigida e sem caminhos."""
    frames = []
    for fs in traceback.extract_tb(exc.__traceback__):
        parts = Path(fs.filename).parts
        if "nexo" in parts:
            module = ".".join(parts[parts.index("nexo") :]).removesuffix(".py")
            frames.append(f"{module}:{fs.name}:{fs.lineno}")
    frames = frames[-12:]
    message = redact(str(exc))
    for token in message.split():
        if "/" in token or "\\" in token:
            message = message.replace(token, "<caminho>")
    report = {
        "type": type(exc).__name__,
        "frames": frames,
        "message": message[:200],
        "command": command,
        "nexo": __version__,
        "os": platform.system().lower(),
        "python": platform.python_version(),
    }
    path = ctx.home / "crash" / f"{int(time.time())}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    with contextlib.suppress(Exception):
        ctx.audit("nexo.crash", {"kind": report["type"], "frames": frames, "command": command, "version": __version__})
    current = tracer(ctx).current()
    if current is not None:
        current.status = STATUS_ERROR
    return report

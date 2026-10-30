"""Monitor sintético das CLIs: tarefa de referência mínima em cada rota; formato divergente → alerta e
bloqueio da rota.

Pensado para rodar diariamente num runner da empresa (`nexo admin monitor synthetic`, ver deploy/ci).
Não gasta mais que
um passo curto por rota. O resultado vai para a telemetria (`monitor.synthetic`); com
`NX_COLLECTOR_MONITOR_AUTOKILL=1` o coletor põe a rota no kill switch central para toda a empresa.
"""

from __future__ import annotations

import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexo.kernel import services
from nexo.kernel.context import AppContext
from nexo.kernel.models.agent import DONE, FILE_CHANGE, INIT, AgentOutcome, AgentRequest
from nexo.kernel.platform import config, killswitch
from nexo.kernel.workspace import git

agents_app = services.load("execution.agents")

TARGET = "nexo-monitor.txt"
PROMPT = (
    "Tarefa de verificação automática do Nexo. Crie o arquivo `nexo-monitor.txt` na raiz do diretório de trabalho "
    "com exatamente o conteúdo `ok` (sem aspas) e termine. Não altere nenhum outro arquivo."
)


@dataclass
class Probe:
    route: str
    status: str  # ok | fail | skipped
    kind: str = ""  # format | behavior | auth | quota | not_installed | error
    detail: str = ""
    seconds: float = 0.0
    checks: dict[str, bool] = field(default_factory=dict)


def _routes(ctx: AppContext, only: list[str] | None) -> list[tuple[str, str, str]]:
    catalog = agents_app.catalog(ctx)
    cfg = dict(config.resolved(ctx).values.get("monitor") or {})
    wanted = only or [str(r) for r in cfg.get("routes") or []]
    seen: set[tuple[str, str]] = set()
    out = []
    for route_id, route in catalog.routes.items():
        key = (str(route.tool), str(route.model))
        if key in seen or (wanted and route_id not in wanted and f"{key[0]}/{key[1]}" not in wanted):
            continue
        seen.add(key)
        out.append((route_id, key[0], key[1]))
    return out


def _probe(ctx: AppContext, adapter: Any, route_id: str, model: str) -> Probe:
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="nexo-monitor-") as tmp:
        work = Path(tmp)
        git.git(["init", "-q"], work)
        req = AgentRequest(
            role="developer",
            prompt=PROMPT,
            workdir=str(work),
            model=model,
            timeout_s=300,
            max_turns=6,
            data_class="public",
            env={"NX_TASK_ID": "MONITOR"},
        )
        caps = adapter.capabilities()
        seen_init = seen_file = False
        outcome = AgentOutcome("error", "technical", "sessão terminou sem resultado")
        session = adapter.start(req, "code-writer")
        for event in session.events():
            seen_init = seen_init or event.kind == INIT
            seen_file = seen_file or event.kind == FILE_CHANGE
            if event.kind == DONE:
                outcome = event.data["outcome"]
        target = work / TARGET
        content_ok = target.is_file() and target.read_text(encoding="utf-8").strip() == "ok"
    checks = {
        "sucesso": outcome.status == "success",
        "arquivo criado": content_ok,
        "consumo informado": bool(outcome.usage),
    }
    if caps.structured_output:  # CLIs com stream estruturado: o formato precisa continuar reconhecível
        checks["evento de início"] = seen_init
        checks["alteração observada"] = seen_file
    seconds = round(time.monotonic() - started, 2)
    if outcome.status == "rate_limited":
        return Probe(route_id, "skipped", "quota", "cota esgotada — sem conclusão sobre o formato", seconds, checks)
    if outcome.error_kind == "auth":
        return Probe(route_id, "fail", "auth", outcome.message[:200], seconds, checks)
    format_checks = [k for k in ("evento de início", "alteração observada", "consumo informado") if k in checks]
    broken = [k for k in format_checks if not checks[k]]
    if outcome.status == "success" and not broken and content_ok:
        return Probe(route_id, "ok", "", "", seconds, checks)
    if broken or "sem resultado" in outcome.message:
        return Probe(
            route_id,
            "fail",
            "format",
            "formato da CLI divergente: falta " + ", ".join(broken or ["resultado final"]),
            seconds,
            checks,
        )
    return Probe(
        route_id, "fail", "behavior", outcome.message[:200] or "arquivo de verificação não criado", seconds, checks
    )


def synthetic(ctx: AppContext, only: list[str] | None = None, auto_block: bool | None = None) -> dict[str, Any]:
    cfg = dict(config.resolved(ctx).values.get("monitor") or {})
    block = bool(cfg.get("auto_block", True)) if auto_block is None else auto_block
    registry = agents_app.adapters(ctx)
    probes: list[Probe] = []
    for route_id, tool, model in _routes(ctx, only):
        adapter = registry.get(tool)
        if adapter is None or not adapter.detect().installed:
            probes.append(Probe(route_id, "skipped", "not_installed", f"{tool} não instalado neste runner"))
            continue
        probe = _probe(ctx, adapter, route_id, model)
        probes.append(probe)
        ctx.audit("monitor.synthetic", {"route": f"{tool}/{model}", "status": probe.status, "kind": probe.kind})
        if probe.status == "fail" and probe.kind == "format":
            from nexo.kernel import services

            notify = services.get("notify.main", "notify")

            notify(ctx, "monitor", f"monitor sintético: {tool}/{model}", probe.detail, ref=route_id)
            if block:
                killswitch.block_local(ctx, f"{tool}/{model}", f"monitor sintético: {probe.detail}")
    failed = [p for p in probes if p.status == "fail"]
    return {"probes": [p.__dict__ for p in probes], "failed": len(failed), "ok": not failed}

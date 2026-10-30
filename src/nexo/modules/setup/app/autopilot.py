"""Piloto automático: o que o Nexo faz sozinho, sem ninguém digitar e sem custo de IA.

Roda em segundo plano quando o console abre (e por `nexo admin autopilot`), cada tarefa no seu intervalo:
- sincroniza aprovações externas e o tracker (quando não é local);
- fecha o custo do mês anterior;
- varre as dependências uma vez por semana;
- confere a calibração do portão uma vez por semana;
- instala a guarda do Nexo no Claude Code do dev, uma vez por dia (`guard.auto`, padrão ligado);
- confere a integridade da trilha de auditoria uma vez por semana;
- coleta lições candidatas (PRs, bugs, ADRs, correções humanas, incidentes) uma vez por semana;
- procura versão nova do Nexo uma vez por dia (quando `update.manifest` está configurado).

O que pede decisão humana não é feito: vira aviso que o console mostra nos próximos passos (vulnerabilidade,
proposta de limiar do portão). Nada aqui chama agente de IA.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext

WEEK = 7 * 86400
STATE_FILE = Path(".nexo") / "autopilot.json"


@dataclass(frozen=True)
class Alert:
    label: str
    command: str
    detail: str = ""


def _state(root: Path) -> dict[str, Any]:
    path = root / STATE_FILE
    try:
        return dict(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else {}
    except (OSError, ValueError):
        return {}


def _save(root: Path, state: dict[str, Any]) -> None:
    path = root / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def alerts(root: Path) -> list[Alert]:
    """Avisos da última rodada (lidos pelo console nos próximos passos)."""
    return [Alert(**a) for a in _state(root).get("alerts", [])]


def run(ctx: AppContext, now: float | None = None, force: bool = False) -> dict[str, Any]:
    """Executa as tarefas vencidas; devolve o que foi feito. Erro de uma tarefa não impede as outras."""
    root = ctx.project_root
    if root is None:
        return {"done": [], "errors": {}}
    now = now if now is not None else time.time()
    state = _state(root)
    last: dict[str, float] = dict(state.get("last", {}))
    done: list[str] = []
    errors: dict[str, str] = {}
    previous = alerts(root)
    new_alerts: list[Alert] = []

    tasks: list[tuple[str, float, Callable[[], list[Alert]]]] = [
        ("sync", 0, lambda: _sync(ctx, root)),
        ("month_close", 86400, lambda: _month_close(ctx)),
        ("calibration", WEEK, lambda: _calibration(ctx)),
        ("guard", 86400, lambda: _guard(ctx)),
        ("audit_verify", WEEK, lambda: _audit_verify(ctx)),
        ("learn_collect", WEEK, lambda: _learn_collect(ctx)),
        ("update_check", 86400, lambda: _update_check(ctx)),
    ]
    for name, every, fn in tasks:
        if not force and now - float(last.get(name, 0)) < every:
            continue
        try:
            new_alerts += fn()
            done.append(name)
        except Exception as exc:  # piloto automático nunca derruba o console
            errors[name] = str(exc)[:200]
        last[name] = now
    # aviso de uma tarefa só é trocado quando ela roda de novo (o da varredura semanal não some no dia seguinte)
    new_alerts = [a for a in previous if _task_of(a) not in done] + new_alerts
    state.update({"last": last, "alerts": [a.__dict__ for a in new_alerts]})
    _save(root, state)
    ctx.audit("autopilot.run", {"done": done, "errors": list(errors), "alerts": len(new_alerts)})
    return {"done": done, "errors": errors, "alerts": [a.__dict__ for a in new_alerts]}


TASK_OF_COMMAND = (
    ("deps", "deps_scan"),
    ("admin gates", "calibration"),
    ("guard", "guard"),
    ("audit", "audit_verify"),
    ("learn", "learn_collect"),
    ("update", "update_check"),
)


def _task_of(alert: Alert) -> str:
    return next((task for prefix, task in TASK_OF_COMMAND if alert.command.startswith(prefix)), "other")


def _audit_verify(ctx: AppContext) -> list[Alert]:
    """Trilha à prova de adulteração: falha de integridade vira problema nos próximos passos."""
    result = ctx.events.verify()
    if result.ok:
        return []
    detail = f"quebra no evento {result.broken_at}: {result.reason}"[:160]
    return [Alert("trilha de auditoria com falha de integridade", "audit verify", detail)]


def _learn_collect(ctx: AppContext) -> list[Alert]:
    """Lições candidatas sem IA; a curadoria (com IA) fica para a pessoa decidir rodar."""
    from nexo.kernel import services

    learning = services.load("knowledge.learning")

    learning.collect(ctx)
    pending = sum(int(v.get("pending") or 0) for v in learning.status(ctx)["sources"].values())
    if not pending:
        return []
    return [Alert(f"{pending} lições candidatas para revisar", "learn review", "o curador propõe; aprovação por PR")]


def _update_check(ctx: AppContext) -> list[Alert]:
    """Versão nova no manifesto assinado da empresa: o console sugere `update` (que confere a assinatura)."""
    from nexo import __version__
    from nexo.kernel.platform import config
    from nexo.modules.setup.app import selfmanage

    source = str((config.resolved(ctx).values.get("update") or {}).get("manifest") or "")
    if not source:
        return []
    available = selfmanage.read_manifest(source).version
    if selfmanage.parse_version(available) <= selfmanage.parse_version(__version__):
        return []
    return [Alert(f"Nexo {available} disponível", "update", f"versão atual {__version__}; atualização assinada")]


def _guard(ctx: AppContext) -> list[Alert]:
    """Guarda no Claude Code: instalada sozinha; se a org desligou o automático ou há a guarda de outro produto,
    vira sugestão (a pessoa decide)."""
    from nexo.modules.setup.app import guard

    result = guard.auto_install(ctx)
    if result == "installed":
        return [Alert("guarda do Nexo instalada no Claude Code", "guard status", "a política vale também fora do Nexo")]
    if result in ("foreign", "error") or (result == "disabled" and guard.required_by_org(ctx)):
        detail = "há outra guarda no Claude Code" if result == "foreign" else "a política vale também fora do Nexo"
        return [Alert("instalar a guarda do Nexo no Claude Code", "guard install", detail)]
    return []


def _sync(ctx: AppContext, root: Path) -> list[Alert]:
    from nexo.kernel import services

    approval = services.load("delivery.approval")
    from nexo.kernel import services

    tracker = services.load("demands.tracker")

    approval.sync(ctx)
    sync = tracker.Sync(ctx, root)
    if sync.enabled:
        sync.retry_pending()
        sync.reconcile()
    return []


def _month_close(ctx: AppContext) -> list[Alert]:
    from nexo.kernel import services
    from nexo.kernel.errors import NexoError

    costs = services.load("finops.costs")

    current = costs.current_month()
    year, month = (int(x) for x in current.split("-"))
    previous = f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"
    if not costs.usage_lines(ctx, previous):  # nada usado no mês: nada a fechar
        return []
    import contextlib

    with contextlib.suppress(NexoError):  # já fechado
        costs.close_month(ctx, previous)
    return []


def _calibration(ctx: AppContext) -> list[Alert]:
    from nexo.kernel import services

    metrics = services.load("finops.metrics")

    result = metrics.calibrate(ctx)
    if result["samples"] < 10 or abs(float(result["proposal"]) - float(result["current"])) < 0.01:
        return []
    return [
        Alert(
            f"portão: calibração sugere limiar {result['proposal']} (hoje {result['current']})",
            "admin gates calibrate",
            f"{result['samples']} decisões humanas — a mudança é aprovada no cérebro da org",
        )
    ]

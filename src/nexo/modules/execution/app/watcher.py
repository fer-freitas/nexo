"""Retomada automática: o que estava bloqueado por cota/orçamento volta a rodar quando libera."""

from __future__ import annotations

import sys
from pathlib import Path

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.modules.execution.app.agents import nexo_command


def resume_ready(ctx: AppContext) -> list[str]:
    from nexo.kernel import services

    approval = services.load("delivery.approval")
    from nexo.kernel import services
    from nexo.modules.execution.app.run import Orchestrator, RunOptions
    from nexo.modules.execution.app.story import StoryRunner

    costs = services.load("finops.costs")
    from nexo.kernel import services

    blocked_items = services.get("finops.metrics", "blocked_items")

    costs.sync_limits(ctx, force=True)  # limite externo atualizado
    approval.sync(ctx)  # sinais externos: histórias aprovadas/paradas, créditos de orçamento
    resumed = []
    from contextlib import suppress

    from nexo.kernel import services

    pr = services.load("delivery.pr")
    from nexo.kernel import services

    clarify = services.load("planning.clarify")

    with suppress(NexoError, OSError, KeyError, ValueError):  # respostas pelo Teams e reação aos PRs
        for item in clarify.apply_remote_answers(ctx):
            ctx.audit("watcher.clarified", {"item": item})
    with suppress(NexoError, OSError, KeyError, ValueError):  # decisões dos portões tomadas pelo Teams
        from nexo.kernel import services

        remote_approval = services.load("delivery.remote_approval")

        for decision in remote_approval.apply(ctx):
            ctx.audit("watcher.approved", decision)
    with suppress(NexoError, OSError, KeyError, ValueError):
        for action in pr.sync(ctx):
            ctx.audit("watcher.pr", action)
    for item, kind in blocked_items(ctx):
        try:
            if kind == "story":
                StoryRunner(ctx, RunOptions()).resume(item)
            else:
                Orchestrator(ctx, RunOptions()).resume(item)
        except NexoError as exc:
            ctx.audit("watcher.error", {"item": item, "error": exc.message})
            continue
        resumed.append(item)
        ctx.audit("watcher.resumed", {"item": item})
    return resumed


def install(ctx: AppContext) -> tuple[Path, str]:
    """Gera o agendamento do SO (não o ativa sozinho: ativar é decisão do dev)."""
    root = ctx.project_root
    if root is None:
        raise NexoError("rode dentro de um projeto Nexo")
    cmd = [*nexo_command(), "budget", "watch"]
    if sys.platform == "darwin":
        path = Path.home() / "Library" / "LaunchAgents" / "com.nexo.watcher.plist"
        args = "".join(f"<string>{a}</string>" for a in cmd)
        content = (
            '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict>'
            f"<key>Label</key><string>com.nexo.watcher</string><key>ProgramArguments</key><array>{args}</array>"
            f"<key>WorkingDirectory</key><string>{root}</string><key>StartInterval</key><integer>1800</integer>"
            f"<key>EnvironmentVariables</key><dict><key>NX_HOME</key><string>{ctx.home}</string></dict>"
            "</dict></plist>\n"
        )
        instructions = f"Ative com: launchctl load -w {path}"
    elif sys.platform == "win32":
        path = ctx.home / "nexo-watcher.cmd"
        content = f'@echo off\ncd /d "{root}"\n{" ".join(cmd)}\n'
        instructions = f'Ative com: schtasks /Create /SC MINUTE /MO 30 /TN "Nexo Watcher" /TR "{path}"'
    else:
        path = ctx.home / "nexo-watcher.cron"
        content = f"*/30 * * * * cd {root} && NX_HOME={ctx.home} {' '.join(cmd)}\n"
        instructions = f"Ative com: crontab {path}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    ctx.audit("watcher.installed", {"path": str(path)})
    return path, instructions

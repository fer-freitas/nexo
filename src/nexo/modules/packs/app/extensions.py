"""`nexo extensions skill|hook|tool add|remove|list|show|sync`."""

from __future__ import annotations

import difflib
import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError, PolicyViolation
from nexo.kernel.extensions.model import Extension, most_similar
from nexo.kernel.platform import config
from nexo.kernel.runtime import yamlfile
from nexo.kernel.security.redaction import find, redact
from nexo.modules.knowledge.app import brain as knowledge_brain
from nexo.modules.packs.adapters import extensions as ext_adapter

LOCAL_SCOPES = ("personal", "project")


@dataclass
class AddReport:
    key: str
    scope: str
    path: str
    installed: dict[str, list[str]] = field(default_factory=dict)
    emulated: dict[str, list[str]] = field(default_factory=dict)
    unavailable: dict[str, list[str]] = field(default_factory=dict)
    similar: list[dict[str, Any]] = field(default_factory=list)
    issue: str | None = None
    proposal_only: bool = False


def _squad(ctx: AppContext) -> str | None:
    value = config.resolved(ctx).values.get("squad")
    return str(value) if value else None


def locations(ctx: AppContext) -> ext_adapter.Locations:
    brain = ctx.brain_dir if ctx.brain_dir.is_dir() else None
    return ext_adapter.locations(ctx.home, brain, ctx.project_root, _squad(ctx))


def tool_paths(ctx: AppContext) -> ext_adapter.ToolPaths:
    agents = config.resolved(ctx).values.get("agents") or {}
    claude = Path(str((agents.get("claude") or {}).get("home") or Path.home() / ".claude")).expanduser()
    copilot = Path(str((agents.get("copilot") or {}).get("home") or Path.home() / ".copilot")).expanduser()
    return ext_adapter.ToolPaths(claude, copilot, ctx.project_root)


def effective(ctx: AppContext) -> list[Extension]:
    return ext_adapter.discover(locations(ctx))


def scan(ext: Extension) -> list[str]:
    """Bloqueia segredos e dados pessoais; e-mail de contato é permitido."""
    folder = Path(ext.path)
    problems: list[str] = []
    for path in folder.rglob("*"):
        if path.is_file() and path.stat().st_size < 1_000_000:
            kinds = {f.kind for f in find(path.read_text(encoding="utf-8", errors="ignore"))} - {"email"}
            if kinds:
                problems.append(f"{path.relative_to(folder)}: {', '.join(sorted(kinds))}")
    return problems


def add(ctx: AppContext, ext_type: str, source: Path, scope: str, name: str | None, extra: dict[str, Any]) -> AddReport:
    folder = ext_adapter.prepare_source(source, ext_type, name, {**extra, "owner": extra.get("owner") or ctx.actor})
    ext = ext_adapter.load(folder, scope)
    problems = scan(ext)
    if problems:
        ctx.audit("policy.violation", {"rule": "SEC-14", "extension": ext.key, "findings": problems})
        raise PolicyViolation("extensão contém segredo ou dado pessoal: " + "; ".join(problems))
    others = [e for e in effective(ctx) if e.key != ext.key and e.meta.type == ext_type]
    similar = most_similar(ext.content, others)
    locs = locations(ctx)
    current = next((e for e in effective(ctx) if e.key == ext.key), None)
    if current is not None and current.meta.locked and current.meta.scope in ("org", "squad"):
        raise PolicyViolation(f"{ext.key} é travada no escopo {current.meta.scope}; proponha a mudança via issue")
    report = AddReport(
        ext.key,
        scope,
        "",
        similar=[{"key": o.key, "scope": o.meta.scope, "similarity": round(score, 2)} for o, score in similar],
    )
    if scope in LOCAL_SCOPES:
        target = ext_adapter.install_copy(ext, locs.dir_for(scope, ext_type, ext.meta.name))
        report.path = str(target)
        installed = sync(ctx)
        for kind in ("installed", "emulated", "unavailable"):
            setattr(
                report,
                kind,
                {
                    t: [n for n in names if n == ext.meta.name]
                    for t, names in getattr(installed, kind).items()
                    if ext.meta.name in names
                },
            )
    else:
        report.proposal_only = True  # squad/org mudam só por PR aprovado no nexo-brain
    report.issue = knowledge_brain.create_issue(
        ctx,
        _issue_title(ext, scope),
        _issue_body(ctx, ext, scope, similar),
        ["nexo", "extensao", ext_type, f"escopo:{scope}"],
    )
    ctx.audit(
        "extension.added",
        {
            "key": ext.key,
            "scope": scope,
            "similar": report.similar,
            "issue": report.issue,
            "proposal_only": report.proposal_only,
        },
    )
    return report


def _issue_title(ext: Extension, scope: str) -> str:
    kind = {"skill": "skill", "hook": "hook", "tool": "tool"}[ext.meta.type]
    return f"Nova {kind}: {ext.meta.name} ({scope})"


def _issue_body(ctx: AppContext, ext: Extension, scope: str, similar: list[tuple[Extension, float]]) -> str:
    lines = [
        f"**Extensão**: `{ext.key}` · versão {ext.meta.version} · escopo `{scope}` · autor `{ctx.actor}`",
        f"**Projeto**: {ctx.project_root.name if ctx.project_root else '—'} · alvos: {', '.join(ext.meta.targets)}",
        f"**Classe de dados**: {ext.meta.data_class} · varredura de segredos/PII: ok",
        "",
        f"{ext.meta.description}",
        "",
        "### Conteúdo",
        "```",
        redact(ext.content)[:5000],
        "```",
    ]
    if similar:
        other, score = similar[0]
        diff = "\n".join(
            difflib.unified_diff(
                other.content.splitlines(), ext.content.splitlines(), other.key, ext.key, lineterm="", n=1
            )
        )
        lines += [
            "",
            f"### Similar a `{other.key}` ({other.meta.scope}) — {score:.0%}",
            "```diff",
            redact(diff)[:4000],
            "```",
        ]
    lines += [
        "",
        "### Decisão do time do Nexo",
        "- [ ] **Promover** (subir de escopo)",
        "- [ ] **Substituir** o padrão atual",
        "- [ ] **Manter local**",
        "- [ ] **Descartar**",
        "",
        "Avaliação sugerida: `nexo eval skill` (F7).",
    ]
    return "\n".join(lines)


def remove(ctx: AppContext, ext_type: str, name: str, scope: str | None) -> list[str]:
    locs = locations(ctx)
    removed: list[str] = []
    for sc in [scope] if scope else list(LOCAL_SCOPES):
        if sc not in LOCAL_SCOPES:
            raise NexoError("extensões de squad/org são removidas por PR no nexo-brain")
        folder = locs.dir_for(sc, ext_type, name)
        if folder.is_dir():
            shutil.rmtree(folder)
            removed.append(str(folder))
    if not removed:
        raise NexoError(f"{ext_type} {name} não encontrada nos escopos locais")
    removed += ext_adapter.uninstall(ext_type, name, tool_paths(ctx))
    sync(ctx)
    ctx.audit("extension.removed", {"key": f"{ext_type}:{name}", "paths": removed})
    return removed


def sync(ctx: AppContext) -> ext_adapter.InstallReport:
    from nexo.modules.execution.app.agents import nexo_command

    extensions = effective(ctx)
    report = ext_adapter.transpile(extensions, tool_paths(ctx), nexo_command())
    ctx.audit(
        "extension.sync",
        {
            "count": len(extensions),
            "installed": report.installed,
            "emulated": report.emulated,
            "unavailable": report.unavailable,
        },
    )
    return report


def show(ctx: AppContext, ext_type: str, name: str) -> dict[str, Any]:
    ext = next((e for e in effective(ctx) if e.key == f"{ext_type}:{name}"), None)
    if ext is None:
        raise NexoError(f"{ext_type} {name} não encontrada")
    return {"meta": ext.meta.model_dump(), "path": ext.path, "content": ext.content}


# ---------------- execução de hooks de extensão (emulação para ferramentas sem hooks nativos)


def hooks_for(ctx: AppContext, event: str, tool: str | None = None) -> list[Extension]:
    return [
        e
        for e in effective(ctx)
        if e.meta.type == "hook"
        and e.meta.hook
        and e.meta.hook.event == event
        and (tool is None or tool in e.meta.targets)
    ]


def run_hook(ext: Extension, payload: dict[str, Any]) -> dict[str, str]:
    """Protocolo canônico: JSON no stdin; `{"decision": "allow"|"deny", "reason": ...}` no stdout."""
    import sys

    if ext.meta.hook is None:
        return {"decision": "allow", "reason": "não é hook"}
    script = Path(ext.path) / "hook.py"
    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=ext.meta.hook.timeout_ms / 1000,
            check=False,
            cwd=ext.path,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip()[:300] or f"saída {proc.returncode}")
        data = json.loads(proc.stdout or "{}") if proc.stdout.strip() else {}
        decision = str(data.get("decision", "allow"))
        return {
            "decision": decision if decision in ("allow", "deny") else "allow",
            "reason": str(data.get("reason", "")),
        }
    except (subprocess.TimeoutExpired, RuntimeError, ValueError) as exc:
        fallback = "deny" if ext.meta.hook.on_error == "deny" else "allow"
        return {"decision": fallback, "reason": f"hook {ext.meta.name} falhou: {exc}"}


def fire(
    ctx: AppContext, event: str, payload: dict[str, Any], tool: str | None = None, ref: str | None = None
) -> list[dict[str, str]]:
    results = []
    for ext in hooks_for(ctx, event, tool):
        result = run_hook(ext, {"event": event, **payload})
        results.append({"hook": ext.meta.name, **result})
        ctx.audit("extension.hook", {"hook": ext.meta.name, "event": event, **result}, ref=ref)
    return results


def load_meta(path: Path) -> dict[str, Any]:
    return yamlfile.load(path)

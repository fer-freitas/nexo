"""Barramento de hooks e o hook nativo `pre-tool-policy`."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import IO, Any

from nexo.kernel.context import AppContext
from nexo.kernel.runtime.resources import load_yaml
from nexo.kernel.security.policy import Decision, Policy, evaluate, normalize
from nexo.store.runs import RunStore

SESSION_START = "session_start"
PRE_SEND = "pre_send"
PRE_TOOL = "pre_tool"
POST_STEP = "post_step"
ON_LIMIT = "on_limit"


def fire(ctx: AppContext, name: str, payload: dict[str, Any], ref: str | None = None) -> None:
    """Registra a execução de um hook builtin na auditoria (os efeitos ficam no chamador)."""
    ctx.audit(f"hook.{name}", payload, ref=ref)


def load_policy(ctx: AppContext) -> Policy:
    """Piso embutido + política da org (só restringe) + MCPs externos aprovados em `org/tools.yaml`."""
    from nexo.kernel.runtime import yamlfile
    from nexo.kernel.security.policy import merge_floor
    from nexo.modules.knowledge.app import content

    data = load_yaml("policy.yaml")  # piso: sempre a baseline embutida
    org_path = ctx.brain_dir / "org" / "security" / "policy.yaml"
    if org_path.is_file():
        data, violations = merge_floor(data, yamlfile.load(org_path))
        if violations:
            _report_floor(ctx, violations)
    external = content.external_mcp(ctx)
    if external:
        mcp = dict(data.get("mcp") or {})
        mcp["allowed_servers"] = [*(mcp.get("allowed_servers") or ["nexo"]), *external]
        # escrita por MCP externo só para os aprovados também em `external_mcp_write`
        mcp["write_allowed"] = [s for s in content.external_mcp_write(ctx) if s in external]
        data["mcp"] = mcp
    return Policy.from_mapping(data)


def _report_floor(ctx: AppContext, violations: list[str]) -> None:
    """Tentativa de afrouxar o piso: auditoria + issue no cérebro, uma vez por conteúdo."""
    import hashlib

    digest = hashlib.sha256("\n".join(sorted(violations)).encode()).hexdigest()[:16]
    marker = ctx.home / "floor-reported" / digest
    if marker.exists():
        return
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("\n".join(violations), encoding="utf-8")
    for v in violations:
        ctx.audit("policy.violation", {"rule": "SEC-76", "reason": f"piso de segurança: {v}"})
    from nexo.modules.knowledge.app import brain

    body = "A política da org tentou afrouxar o piso de segurança; os itens abaixo foram ignorados:\n\n" + "\n".join(
        f"- {v}" for v in violations
    )
    brain.create_issue(ctx, "Piso de segurança: política da org ignorada em parte", body, ["nexo", "seguranca"])


def agent_env_allow(ctx: AppContext, policy: Policy, tool: str, ref: str | None) -> tuple[str, ...]:
    """Permissões de ambiente do agente + auditoria dos NOMES removidos (valores nunca são registrados)."""
    import os

    from nexo.kernel.security.environment import filter_env

    allow = policy.agent_env_allow(tool)
    _kept, removed = filter_env(os.environ, allow)
    if removed:
        ctx.audit("agent.env_filtered", {"tool": tool, "removed": removed[:100], "count": len(removed)}, ref=ref)
    return allow


def check_action(
    ctx: AppContext, policy: Policy, run_id: str, workdir: str, tool: str, tool_input: dict[str, Any], profile: str
) -> Decision:
    action = normalize(tool, tool_input, policy)
    decision = evaluate(action, workdir, policy, profile)
    if not decision.allow:
        ctx.audit(
            "policy.violation",
            {"rule": decision.rule, "reason": decision.reason, "tool": tool, "input": tool_input, "profile": profile},
            ref=run_id,
        )
    return decision


def run_pre_tool_policy(
    ctx: AppContext, run_id: str, stdin: IO[str] | None = None, stdout: IO[str] | None = None
) -> int:
    """Protocolo de hook PreToolUse do Claude Code: lê JSON do stdin, responde negação em JSON."""
    src = stdin or sys.stdin
    dst = stdout or sys.stdout
    try:
        payload = json.loads(src.read() or "{}")
    except ValueError:
        payload = {}
    run = RunStore(ctx.conn).get_run(run_id)
    tool = str(payload.get("tool_name") or "")
    tool_input = payload.get("tool_input") or {}
    if run is None or not run.worktree:
        decision = Decision(False, "execução desconhecida para o hook do Nexo", "SEC-20")
        ctx.audit("policy.violation", {"rule": decision.rule, "reason": decision.reason, "tool": tool}, ref=run_id)
    else:
        profile, locked = run_policy(Path(run.worktree), run_id)
        policy = load_policy(ctx).with_locked(locked)
        decision = check_action(ctx, policy, run_id, run.worktree, tool, dict(tool_input), profile)
    if decision.allow:
        return 0
    response = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": f"Nexo ({decision.rule}): {decision.reason}",
        }
    }
    dst.write(json.dumps(response, ensure_ascii=False))
    dst.flush()
    return 0


def run_policy(worktree: Path, run_id: str) -> tuple[str, frozenset[str]]:
    """Perfil e arquivos travados gravados pelo orquestrador em `.nexo/runs/<run>/policy.json`."""
    root = worktree.parents[2] if worktree.parent.name == "worktrees" else worktree
    path = root / ".nexo" / "runs" / run_id / "policy.json"
    if not path.is_file():
        return "code-writer", frozenset()
    data = json.loads(path.read_text(encoding="utf-8"))
    return str(data.get("profile", "code-writer")), frozenset(str(x) for x in data.get("locked", []))


def denial_count(ctx: AppContext, run_id: str) -> int:
    """Negações desde o início da execução ou desde a última retomada: quem retoma já tratou a causa (achado no e2e
    Flutter: a retomada escalava de novo na hora, sem nenhuma negação nova)."""
    count = 0
    for e in ctx.events.recent(limit=500, ref=run_id):
        if e.type == "run.resume":
            count = 0
        elif e.type == "policy.violation":
            count += 1
    return count


def worktree_path(ctx: AppContext, run_id: str) -> Path | None:
    run = RunStore(ctx.conn).get_run(run_id)
    return Path(run.worktree) if run and run.worktree else None

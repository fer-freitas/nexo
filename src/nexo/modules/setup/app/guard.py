"""Governança da IA usada fora do Nexo: a guarda do Nexo instalada no próprio Claude Code do dev.

`nexo guard install` acrescenta ao `settings.json` do usuário no Claude Code um hook `PreToolUse` que chama
`nexo hook run guard`: toda ação do agente, mesmo numa sessão aberta sem o Nexo, passa pela mesma política de
segurança da empresa (piso embutido + `org/security/policy.yaml`): comandos proibidos, arquivos sensíveis, rede,
segredos. Negações ficam na trilha de auditoria. Sessões dentro de worktrees do Nexo seguem o hook da execução.

O Copilot CLI não tem hooks (a governança dele é a política da org no GitHub); o Devin CLI tem hooks sem formato
documentado — ambos ficam registrados como pendência.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import IO, Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config, flags
from nexo.kernel.security.policy import Decision

MARK = "hook run guard"


def settings_path(ctx: AppContext, env: dict[str, str] | None = None) -> Path:
    env = dict(os.environ if env is None else env)
    if env.get("CLAUDE_CONFIG_DIR"):
        return Path(env["CLAUDE_CONFIG_DIR"]) / "settings.json"
    home = (config.resolved(ctx).values.get("agents") or {}).get("claude", {}).get("home")
    return (Path(str(home)).expanduser() if home else Path.home() / ".claude") / "settings.json"


def _command() -> str:
    from nexo.kernel.runtime.system import shell_join
    from nexo.modules.execution.app.agents import nexo_command

    return shell_join([*nexo_command(), "hook", "run", "guard"])


def _load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except ValueError as exc:
        raise NexoError(f"{path} não é um JSON válido; corrija antes de instalar a guarda ({exc})") from exc
    if not isinstance(data, dict):
        raise NexoError(f"{path} não é um objeto JSON")
    return data


def _entries(data: dict[str, Any]) -> list[dict[str, Any]]:
    return list(((data.get("hooks") or {}).get("PreToolUse")) or [])


def _is_nexo(command: str) -> bool:
    """O hook chama o Nexo (`-m nexo` ou o executável `nexo`), não outro produto com hook parecido."""
    words = command.replace("\\", "/").split()
    return MARK in command and any(w == "nexo" or w.endswith("/nexo") or w.endswith("/nexo.exe") for w in words)


def _is_ours(entry: dict[str, Any]) -> bool:
    return any(_is_nexo(str(h.get("command", ""))) for h in entry.get("hooks") or [])


def _is_foreign(entry: dict[str, Any]) -> bool:
    """Guarda de outro produto (ou de uma instalação com outro nome) no mesmo Claude Code: nunca é trocada sozinha."""
    return any(
        MARK in str(h.get("command", "")) and not _is_nexo(str(h.get("command", ""))) for h in entry.get("hooks") or []
    )


def foreign_installed(ctx: AppContext) -> bool:
    try:
        return any(_is_foreign(e) for e in _entries(_load(settings_path(ctx))))
    except NexoError:
        return False


def auto_install(ctx: AppContext) -> str:
    """Piloto automático: instala a guarda sozinho quando a org deixa (`guard.auto`, padrão ligado) e o Claude Code
    está nesta máquina. Devolve o que aconteceu: installed | present | disabled | no_claude | foreign | error."""
    if not flags.enabled(ctx, "guard"):
        return "disabled"
    if installed(ctx):
        return "present"
    if not bool((config.resolved(ctx).values.get("guard") or {}).get("auto", True)):
        return "disabled"
    if shutil.which("claude") is None and not settings_path(ctx).parent.is_dir():
        return "no_claude"
    if foreign_installed(ctx):
        return "foreign"
    try:
        install(ctx)
    except (NexoError, OSError):
        return "error"
    ctx.audit("guard.auto_installed", {"tool": "claude", "settings": str(settings_path(ctx))})
    return "installed"


def installed(ctx: AppContext) -> bool:
    try:
        return any(_is_ours(e) for e in _entries(_load(settings_path(ctx))))
    except NexoError:
        return False


def install(ctx: AppContext) -> dict[str, Any]:
    flags.require(ctx, "guard")
    path = settings_path(ctx)
    data = _load(path)
    if path.is_file() and not path.with_suffix(".json.nexo-bak").exists():
        shutil.copy2(path, path.with_suffix(".json.nexo-bak"))  # cópia do original, uma vez
    entries = [e for e in _entries(data) if not _is_ours(e)]
    entries.append({"matcher": "*", "hooks": [{"type": "command", "command": _command(), "timeout": 20}]})
    hooks = dict(data.get("hooks") or {})
    hooks["PreToolUse"] = entries
    data["hooks"] = hooks
    added_env = False
    if os.environ.get("NX_HOME") and (data.get("env") or {}).get("NX_HOME") != str(ctx.home):
        data["env"] = {**dict(data.get("env") or {}), "NX_HOME": str(ctx.home)}  # a guarda acha a mesma pasta
        added_env = True
    if added_env:
        (ctx.home / "guard.json").write_text(json.dumps({"added_env": True}), encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ctx.audit("guard.installed", {"tool": "claude", "settings": str(path)})
    return status(ctx)


def uninstall(ctx: AppContext) -> dict[str, Any]:
    path = settings_path(ctx)
    data = _load(path)
    entries = [e for e in _entries(data) if not _is_ours(e)]
    state = ctx.home / "guard.json"
    if state.is_file() and json.loads(state.read_text(encoding="utf-8")).get("added_env"):
        env = dict(data.get("env") or {})
        env.pop("NX_HOME", None)
        if env:
            data["env"] = env
        else:
            data.pop("env", None)
        state.unlink()
    if "hooks" in data:
        data["hooks"]["PreToolUse"] = entries
        if not entries:
            data["hooks"].pop("PreToolUse")
        if not data["hooks"]:
            data.pop("hooks")
    if path.is_file():
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    required = required_by_org(ctx)
    ctx.audit("guard.uninstalled", {"tool": "claude", "required": required})
    return status(ctx)


def required_by_org(ctx: AppContext) -> bool:
    from nexo.kernel.config.cascade import resolve

    layers = [layer for layer in config.load_layers(ctx) if layer.level in ("default", "org", "squad")]
    return bool((resolve(layers).values.get("guard") or {}).get("required"))


def status(ctx: AppContext) -> dict[str, Any]:
    return {
        "claude": {"installed": installed(ctx), "settings": str(settings_path(ctx))},
        "copilot": {"installed": False, "supported": False, "note": "Copilot CLI sem hooks: política da org no GitHub"},
        "devin": {"installed": False, "supported": False, "note": "hooks do Devin sem formato documentado"},
        "required": required_by_org(ctx),
        "enabled": flags.enabled(ctx, "guard"),
    }


def run_hook(ctx: AppContext, stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> int:
    """Protocolo PreToolUse do Claude Code para sessões fora do Nexo: nega o que a política da empresa proíbe."""
    from nexo.kernel.security.policy import evaluate_guard, normalize
    from nexo.modules.execution.app.hooks import load_policy

    src, dst = stdin or sys.stdin, stdout or sys.stdout
    try:
        payload = json.loads(src.read() or "{}")
    except ValueError:
        payload = {}
    if not flags.enabled(ctx, "guard"):
        return 0
    cwd = str(payload.get("cwd") or Path.cwd())
    if "/.nexo/worktrees/" in cwd.replace("\\", "/") + "/":
        return 0  # sessão do próprio Nexo: vale o hook da execução
    tool = str(payload.get("tool_name") or "")
    tool_input = dict(payload.get("tool_input") or {})
    ref = "guard:" + str(payload.get("session_id") or "?")[:40]
    policy = load_policy(ctx)
    decision: Decision = evaluate_guard(normalize(tool, tool_input, policy), cwd, policy)
    if decision.allow:
        return 0
    ctx.audit(
        "policy.violation",
        {"rule": decision.rule, "reason": decision.reason, "tool": tool, "input": tool_input, "source": "guard"},
        ref=ref,
    )
    dst.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Nexo ({decision.rule}): {decision.reason}",
                }
            },
            ensure_ascii=False,
        )
    )
    dst.flush()
    return 0

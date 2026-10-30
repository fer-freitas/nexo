"""Conteúdo vindo do cérebro: prompts com contrato, compatibilidade, hooks.yaml, tools.yaml,
migração da baseline embutida e diferença entre cérebro e baseline.

O binário leva a baseline de fábrica e o piso de segurança; fora isso, vale o cérebro."""

from __future__ import annotations

import difflib
import hashlib
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexo import __version__
from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.runtime import yamlfile
from nexo.kernel.runtime.resources import embedded_dir, read_text

SCHEMA_VERSIONS = (1,)
COMPAT_FILE = "brain.yaml"
SECURITY_HOOKS = ("pre_send", "pre_tool", "post_edit", "post_step", "pre_commit")
OPTIONAL_HOOKS = ("session_start", "story_notify")
MCP_TOOLS = (
    "get_task_context",
    "search_brain",
    "report_progress",
    "request_clarification",
    "run_checks",
    "record_decision",
    "check_dependency",
)
# Conteúdo migrável: caminho no cérebro = caminho na baseline embutida.
CONTENT_FILES = (
    "routing.yaml",
    "risk-map.yaml",
    "workflows/task.yaml",
    "workflows/story.yaml",
    "workflows/mappings/github.yaml",
    "workflows/mappings/file.yaml",
    "recipes/common.yaml",
    "recipes/python.yaml",
    "recipes/dotnet.yaml",
    "recipes/react.yaml",
    "agents/claude/patterns.yaml",
    "agents/copilot/patterns.yaml",
    "agents/copilot/capabilities.yaml",
    "agents/devin/patterns.yaml",
    "agents/devin/capabilities.yaml",
)


def embedded_skills() -> list[str]:
    return sorted(p.name for p in embedded_dir("skills").iterdir() if p.is_dir() and p.name != "__pycache__")


def _prompt_names() -> list[str]:
    return sorted(p.stem for p in embedded_dir("prompts").glob("*.md"))


def placeholders(text: str) -> set[str]:
    """Variáveis `{nome}` de um template `str.format` (chaves escapadas `{{ }}` não contam)."""
    names = set()
    for _, field, _, _ in string.Formatter().parse(text):
        if field:
            names.add(field.split(".")[0].split("[")[0])
    return names


def contract(role: str) -> set[str]:
    """O contrato de um prompt é o conjunto de variáveis que o código entrega (as da baseline)."""
    return placeholders(read_text(f"prompts/{role}.md"))


def _check_prompt(role: str, text: str) -> str | None:
    try:
        found = placeholders(text)
    except ValueError as exc:
        return f"prompts/{role}.md: template inválido ({exc})"
    expected = contract(role)
    if found != expected:
        missing, extra = sorted(expected - found), sorted(found - expected)
        return f"prompts/{role}.md: contrato quebrado (faltam {missing or '—'}; desconhecidas {extra or '—'})"
    return None


def _brain_version(ctx: AppContext) -> str:
    from nexo.kernel import brain as brain_adapter

    status = brain_adapter.cached_status(ctx.brain_dir) or {}
    return str(status.get("version") or "?")


def prompt(ctx: AppContext, role: str, ref: str | None = None) -> str:
    """Prompt do papel: do cérebro se cumprir o contrato, senão a baseline. Auditado por hash."""
    text, origin = read_text(f"prompts/{role}.md"), "embutido"
    path = ctx.brain_dir / "prompts" / f"{role}.md"
    if path.is_file():
        candidate = path.read_text(encoding="utf-8")
        problem = _check_prompt(role, candidate)
        if problem:
            ctx.audit("brain.content_rejected", {"path": f"prompts/{role}.md", "reason": problem}, ref=ref)
        else:
            text, origin = candidate, f"brain@{_brain_version(ctx)}"
    digest = hashlib.sha256(text.encode()).hexdigest()[:16]
    ctx.audit("prompt.used", {"role": role, "sha256": digest, "source": origin}, ref=ref)
    return text


# ---------------------------------------------------------------- compatibilidade e validação


def _version_tuple(value: str) -> tuple[int, ...]:
    return tuple(int(p) for p in value.split(".") if p.isdigit())


def compat_errors(root: Path) -> list[str]:
    data = yamlfile.load(root / COMPAT_FILE) or yamlfile.load(root / "nexo-brain.yaml")  # nome anterior
    if not data:
        return []  # cérebro anterior à esquema 1, sem versão mínima
    errors = []
    schema = int(data.get("schema_version", 1))
    if schema not in SCHEMA_VERSIONS:
        errors.append(f"{COMPAT_FILE}: schema_version {schema} não suportado por esta versão ({SCHEMA_VERSIONS})")
    minimum = str(data.get("min_nexo") or data.get("min_nexo") or "")
    if minimum and _version_tuple(__version__) < _version_tuple(minimum):
        errors.append(f"{COMPAT_FILE}: exige Nexo {minimum} ou superior (instalado {__version__}) — rode `nexo update`")
    return errors


def _hooks_errors(data: dict[str, Any]) -> list[str]:
    errors = []
    for name in data.get("disable") or []:
        if name in SECURITY_HOOKS:
            errors.append(f"org/hooks.yaml: {name} é hook de segurança e não pode ser desligado")
        elif name not in OPTIONAL_HOOKS:
            errors.append(f"org/hooks.yaml: hook desconhecido {name} (opcionais: {', '.join(OPTIONAL_HOOKS)})")
    if "handoff" in data and not isinstance(data["handoff"], bool):
        errors.append("org/hooks.yaml: handoff deve ser true ou false")
    return errors


def _tools_errors(data: dict[str, Any]) -> list[str]:
    errors = []
    for role, tools in (data.get("roles") or {}).items():
        unknown = sorted(set(tools or []) - set(MCP_TOOLS))
        if unknown:
            errors.append(f"org/tools.yaml: papel {role} com tools desconhecidas {unknown}")
    return errors


def validate_tree(root: Path) -> list[str]:
    """Validação de conteúdo antes de aceitar um cérebro novo no pull (a assinatura já foi verificada)."""
    errors = compat_errors(root)
    for path in sorted((root / "prompts").glob("*.md")):
        if path.stem in _prompt_names():
            problem = _check_prompt(path.stem, path.read_text(encoding="utf-8"))
            if problem:
                errors.append(problem)
    errors += _hooks_errors(yamlfile.load(root / "org" / "hooks.yaml"))
    errors += _tools_errors(yamlfile.load(root / "org" / "tools.yaml"))
    try:  # S-05: kill switch do cérebro precisa ser legível (versões válidas)
        from nexo.kernel.security.killswitch import parse as parse_killswitch

        parse_killswitch(yamlfile.load(root / "org" / "kill-switch.yaml"), "cérebro")
    except (ValueError, TypeError) as exc:
        errors.append(f"org/kill-switch.yaml: {exc}")
    from nexo.kernel.config.flags import validate as validate_flags

    for flags_file in [root / "org" / "flags.yaml", *sorted((root / "squads").glob("*/flags.yaml"))]:
        data = yamlfile.load(flags_file).get("flags")
        if data is not None:
            where = flags_file.relative_to(root).as_posix()
            errors += validate_flags(dict(data), where) if isinstance(data, dict) else [f"{where}: `flags` é mapa"]
    revoked = yamlfile.load(root / "org" / "security" / "revoked-signers.yaml").get("revoked")
    if revoked is not None and not (isinstance(revoked, list) and all(isinstance(x, str) for x in revoked)):
        errors.append("org/security/revoked-signers.yaml: `revoked` deve ser lista de impressões digitais")
    for meta in sorted(
        [*(root / "extensions").glob("*/*/extension.yaml"), *(root / "extensions").glob("*/*/nexo.yaml")]
    ):
        rollout = (yamlfile.load(meta) or {}).get("rollout")
        if rollout not in (None, "all") and not (isinstance(rollout, dict) and isinstance(rollout.get("squads"), list)):
            errors.append(f"{meta.relative_to(root).as_posix()}: rollout deve ser 'all' ou {{squads: [...]}}")
    return errors


# ---------------------------------------------------------------- hooks.yaml e tools.yaml


def hooks_cfg(ctx: AppContext) -> dict[str, Any]:
    data = yamlfile.load(ctx.brain_dir / "org" / "hooks.yaml")
    disabled = [h for h in data.get("disable") or [] if h in OPTIONAL_HOOKS]  # segurança nunca desliga
    return {"disable": disabled, "handoff": bool(data.get("handoff", True))}


def hook_enabled(ctx: AppContext, name: str) -> bool:
    return name in SECURITY_HOOKS or name not in hooks_cfg(ctx)["disable"]


def tools_for_role(ctx: AppContext, role: str | None) -> set[str] | None:
    """Tools do nexo-mcp liberadas ao papel (`org/tools.yaml › roles`); None = todas."""
    roles = (yamlfile.load(ctx.brain_dir / "org" / "tools.yaml").get("roles")) or {}
    if role is None or role not in roles:
        return None
    return {t for t in roles[role] or [] if t in MCP_TOOLS}


def external_mcp(ctx: AppContext) -> list[str]:
    """MCPs externos aprovados pela Segurança (`org/tools.yaml › external_mcp`)."""
    return [str(s) for s in yamlfile.load(ctx.brain_dir / "org" / "tools.yaml").get("external_mcp") or []]


def external_mcp_write(ctx: AppContext) -> list[str]:
    """MCPs externos que a Segurança liberou para escrever (`org/tools.yaml › external_mcp_write`)."""
    return [str(s) for s in yamlfile.load(ctx.brain_dir / "org" / "tools.yaml").get("external_mcp_write") or []]


def effective_hooks(ctx: AppContext) -> dict[str, Any]:
    cfg = hooks_cfg(ctx)
    return {
        "security": list(SECURITY_HOOKS),
        "optional": {h: h not in cfg["disable"] for h in OPTIONAL_HOOKS},
        "handoff": cfg["handoff"],
    }


# ---------------------------------------------------------------- migração e diferença


@dataclass(frozen=True)
class Owners:
    nexo: str = "@time-nexo"
    security: str = "@seguranca"
    stacks: tuple[tuple[str, str], ...] = ()


def _skill_meta(name: str, skill_md: str) -> str:
    from nexo.kernel.extensions.model import frontmatter_description

    description = frontmatter_description(skill_md).replace('"', "'")
    return (
        f'name: {name}\ntype: skill\nscope: org\nversion: 1.0.0\nowner: time-nexo\ndescription: "{description}"\n'
        "targets: [claude, copilot, devin]\nrollout: all\n"
    )


KILL_SWITCH_TEMPLATE = """# Kill switch: bloqueios em incidente. Checado no roteamento e no início de cada passo.
# Efeito imediato para toda a empresa: use também o central (PUT /v2/admin/kill-switch, admin ou Segurança).
# Exemplos:
#   tools: {devin: "INC-123 instabilidade do fornecedor"}
#   models: {"claude/opus": "INC-124 regressão de qualidade"}
#   nexo_versions: {"<0.15.1": "defeito de segurança corrigido na 0.15.1"}
#   brain_versions: {"2026.09.25-3": "prompt com problema"}
tools: {}
models: {}
nexo_versions: {}
brain_versions: {}
"""
REVOKED_TEMPLATE = """# Chaves de assinatura revogadas: impressões digitais de `nexo brain pubkey`.
# Uma vez vista num cérebro verificado, a revogação vale para sempre naquela instalação.
revoked: []
"""


def flags_template() -> str:
    """`org/flags.yaml` com o catálogo inteiro comentado."""
    from nexo.kernel.config.flags import CATALOG

    lines = [
        "# Feature flags da empresa. Por squad: squads/<squad>/flags.yaml com o mesmo formato.",
        "# Para efeito imediato use o central (nexo admin flags set / dashboard › Feature flags), que tem precedência.",
        "# O piso de segurança não tem flag. Descomente para desligar/ligar:",
        "flags: {}",
    ]
    lines += [f"#   {f.key}: false  # {f.title}" for f in CATALOG]
    return "\n".join(lines) + "\n"


def baseline_files() -> dict[str, str]:
    """Todo o conteúdo migrável da baseline embutida, no layout do cérebro."""
    files = {rel: read_text(rel) for rel in CONTENT_FILES}
    for tpl in sorted(embedded_dir("templates").glob("*.md")):  # templates de artefatos
        files[f"templates/{tpl.name}"] = tpl.read_text(encoding="utf-8")
    for det in sorted(embedded_dir("insights").glob("*.yaml")):  # detectores de insight (usados pelo coletor)
        files[f"insights/{det.name}"] = det.read_text(encoding="utf-8")
    for std in sorted(embedded_dir("standards").glob("*.yaml")):  # regras do padrão de código
        files[f"standards/{std.name}"] = std.read_text(encoding="utf-8")
    for role in _prompt_names():
        files[f"prompts/{role}.md"] = read_text(f"prompts/{role}.md")
    skills = embedded_dir("skills")
    for folder in sorted(p for p in skills.iterdir() if p.is_dir()):
        for f in sorted(folder.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                files[f"extensions/skills/{folder.name}/{f.relative_to(folder).as_posix()}"] = f.read_text("utf-8")
        files[f"extensions/skills/{folder.name}/extension.yaml"] = _skill_meta(
            folder.name, (folder / "SKILL.md").read_text(encoding="utf-8")
        )
    from nexo.kernel.brain import BRAIN_GITIGNORE

    files[".gitignore"] = BRAIN_GITIGNORE
    files["org/kill-switch.yaml"] = KILL_SWITCH_TEMPLATE
    files["org/flags.yaml"] = flags_template()
    files["org/security/revoked-signers.yaml"] = REVOKED_TEMPLATE
    files["org/security/policy.yaml"] = (
        "# Política da org. O Nexo mescla com o PISO embutido: aqui só se RESTRINGE (acrescentar negações e\n"
        "# caminhos proibidos, remover comandos permitidos). Afrouxar o piso é ignorado e vira violação.\n"
        + read_text("policy.yaml")
    )
    return files


def _codeowners(owners: Owners) -> str:
    lines = [
        "# Donos por pasta. Toda mudança passa por PR; a assinatura do manifesto é do time do Nexo.",
        f"* {owners.nexo}",
        f"/org/security/ {owners.security} {owners.nexo}",
        f"/org/tools.yaml {owners.security} {owners.nexo}",
        f"/org/hooks.yaml {owners.security} {owners.nexo}",
        f"/risk-map.yaml {owners.security} {owners.nexo}",
        f"/routing.yaml {owners.nexo}",
        f"/prompts/ {owners.nexo}",
        f"/templates/ {owners.nexo}",
        f"/extensions/skills/secure-coding/ {owners.security} {owners.nexo}",
    ]
    for stack, owner in owners.stacks:
        for skill in (f"{stack}-standards", f"testing-{stack}"):
            lines.append(f"/extensions/skills/{skill}/ {owner} {owners.nexo}")
        lines.append(f"/recipes/{stack}.yaml {owner} {owners.nexo}")
    return "\n".join(lines) + "\n"


def migrate(target: Path, owners: Owners, overwrite: bool = False) -> list[str]:
    """Escreve a baseline embutida no clone do cérebro (para virar PR). Não sobrescreve sem `overwrite`."""
    if not (target / ".git").exists():
        raise NexoError(f"{target} não é um clone git do nexo-brain")
    files = baseline_files()
    files[COMPAT_FILE] = f"schema_version: 1\nmin_nexo: {__version__}\n"
    files["org/hooks.yaml"] = (
        "# Hooks de segurança (pre_send, pre_tool, post_edit, post_step, pre_commit) nunca desligam.\n"
        f"# Opcionais: {', '.join(OPTIONAL_HOOKS)}.\ndisable: []\nhandoff: true\n"
    )
    files["org/tools.yaml"] = (
        "# Tools do nexo-mcp por papel (sem o papel aqui = todas) e MCPs externos aprovados pela Segurança.\n"
        "roles:\n  reviewer: [get_task_context, search_brain, run_checks, report_progress]\n"
        "  security-reviewer: [get_task_context, search_brain, run_checks, report_progress]\n"
        "external_mcp: []\n"
    )
    files["CODEOWNERS"] = _codeowners(owners)
    written = []
    for rel, content in sorted(files.items()):
        path = target / rel
        if path.exists() and not overwrite and rel != "CODEOWNERS":
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(rel)
    return written


def diff(ctx: AppContext, show: str | None = None) -> dict[str, Any]:
    """Diferença entre o cérebro em cache e a baseline embutida (o que o cérebro muda de fato)."""
    brain = ctx.brain_dir
    if not brain.is_dir():
        raise NexoError("cérebro não baixado (nexo brain pull)")
    rows = []
    for rel, base in sorted(baseline_files().items()):
        path = brain / rel
        if not path.is_file():
            rows.append({"path": rel, "status": "só na baseline"})
        elif path.read_text(encoding="utf-8") != base:
            rows.append({"path": rel, "status": "alterado no cérebro"})
    known = set(baseline_files())
    for folder in ("prompts", "extensions/skills"):
        for path in sorted((brain / folder).rglob("*")):
            rel = path.relative_to(brain).as_posix()
            if path.is_file() and rel not in known and not rel.endswith("README.md"):
                rows.append({"path": rel, "status": "só no cérebro"})
    result: dict[str, Any] = {"version": _brain_version(ctx), "changes": rows}
    if show:
        before = baseline_files().get(show, "").splitlines(keepends=True)
        after_path = brain / show
        after = after_path.read_text(encoding="utf-8").splitlines(keepends=True) if after_path.is_file() else []
        result["diff"] = "".join(difflib.unified_diff(before, after, f"baseline/{show}", f"brain/{show}"))
    return result

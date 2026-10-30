"""Diagnóstico do ambiente (`nexo doctor`)."""

from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

from nexo.kernel.context import AppContext
from nexo.kernel.platform import config
from nexo.kernel.runtime.system import ToolProbe, probe

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass(frozen=True)
class Check:
    name: str
    category: str
    status: str
    detail: str
    required: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


# (executável, categoria, obrigatório, observação quando ausente)
TOOLS: tuple[tuple[str, str, bool, str], ...] = (
    ("git", "base", True, "necessário para checkpoints e worktrees"),
    ("claude", "agente", False, "Claude Code CLI — usado a partir da fase 1"),
    ("copilot", "agente", False, "GitHub Copilot CLI — usado a partir da fase 1"),
    ("devin", "agente", False, "Devin CLI — usado a partir da fase 1"),
    ("ffmpeg", "planejamento", False, "necessário para vídeo/áudio na fase 2"),
    ("gitleaks", "segurança", False, "varredura de segredos (fase 3; recomendado já)"),
    ("dotnet", "stack", False, "receita .NET"),
    ("node", "stack", False, "receitas React e Angular"),
    ("java", "stack", False, "receita Java (JDK 21)"),
    ("mvn", "stack", False, "receita Java com Maven (dispensável se o projeto tiver mvnw)"),
    ("gradle", "stack", False, "receita Java com Gradle (dispensável se o projeto tiver gradlew)"),
    ("flutter", "stack", False, "receita Flutter"),
    ("osv-scanner", "segurança", False, "dependências vulneráveis de Flutter e Java"),
    ("python", "stack", False, "receita Python (cria o .venv do projeto com pip)"),
    ("pip-audit", "segurança", False, "instalado sob demanda em ~/.nexo-home/tools (vulnerabilidades Python)"),
    ("semgrep", "segurança", False, "instalado sob demanda em ~/.nexo-home/tools (SAST)"),
)

EXE_TOOL: dict[str, str] = {}  # executável → nome da ferramenta no registro de agentes


def _tool_check(spec: tuple[str, str, bool, str], result: ToolProbe) -> Check:
    name, category, required, note = spec
    if result.installed:
        return Check(name, category, OK, result.version or "instalado (versão não identificada)", required)
    return Check(name, category, FAIL if required else WARN, f"não encontrado — {note}", required)


def run_checks(ctx: AppContext) -> list[Check]:
    checks: list[Check] = []
    py_ok = sys.version_info >= (3, 12)
    checks.append(
        Check("python", "base", OK if py_ok else FAIL, sys.version.split()[0], True),
    )
    with ThreadPoolExecutor(max_workers=len(TOOLS)) as pool:
        probes = list(pool.map(lambda spec: probe(spec[0]), TOOLS))
    checks.extend(_tool_check(spec, res) for spec, res in zip(TOOLS, probes, strict=True))

    home_ok = _writable(ctx)
    checks.append(Check("nexo_home", "base", OK if home_ok else FAIL, str(ctx.home), True))
    if home_ok:
        result = ctx.events.verify()
        detail = f"{result.checked} eventos" if result.ok else f"quebra no evento {result.broken_at}: {result.reason}"
        checks.append(Check("audit", "segurança", OK if result.ok else FAIL, detail, True))
        checks.append(_identity_check(ctx))
        checks.append(_hooks_check(ctx))
        checks.append(_telemetry_check(ctx))
        checks.append(_isolation_check(ctx))
        checks.append(_guard_check(ctx))
        checks.append(_pricing_check(ctx))
        checks.append(_gitignore_check(ctx))

    agents = [c for c in checks if c.category == "agente" and c.status == OK]
    checks.extend(_auth_checks(ctx, [c.name for c in agents]))
    checks.append(
        Check(
            "agentes",
            "agente",
            OK if agents else WARN,
            f"{len(agents)} ferramenta(s) disponível(is)" if agents else "nenhuma CLI de agente encontrada",
            False,
        )
    )
    brain = ctx.brain_dir
    checks.append(
        Check(
            "brain",
            "conhecimento",
            OK if brain.is_dir() else WARN,
            (str(brain) if brain.is_dir() else "nexo-brain não configurado (config brain.repo e nexo brain pull)")
            + (" · âncora da org: fixa" if config.anchor() else " · âncora da org: ausente (sandbox)"),
            False,
        )
    )
    checks.append(
        Check(
            "project",
            "projeto",
            OK if ctx.project_root else WARN,
            str(ctx.project_root) if ctx.project_root else "fora de um projeto Nexo (rode `nexo init`)",
            False,
        )
    )
    checks += _team_checks(ctx)
    return checks


def _team_checks(ctx: AppContext) -> list[Check]:
    """Repositório do time, fila de envio e última passada da linhagem."""
    from nexo.kernel import services
    from nexo.kernel.workspace.specs import project_settings

    out: list[Check] = []
    if ctx.project_root is not None:
        settings = project_settings(ctx.project_root)
        if settings.shared:
            exists = settings.team_dir().is_dir()
            detail = f"time {settings.team_name}" if exists else "rode `nexo join` ou `nexo team link`"
            out.append(Check("team", "repositório do time", OK if exists else WARN, detail, False))
            if exists:
                pending = services.load("demands.core").store(ctx).pending()
                out.append(
                    Check(
                        "team-queue",
                        "envio ao time",
                        WARN if pending else OK,
                        f"{pending} gravação(ões) ainda nesta máquina" if pending else "tudo enviado",
                        False,
                    )
                )
        else:
            out.append(Check("team", "repositório do time", WARN, "sem time: as demandas ficam só nesta máquina", False))
    if services.available("lineage.app"):
        state = services.load("lineage.app").agent_state(ctx)
        if state["last_error"]:
            detail = f"último erro: {state['last_error']}"[:120]
        elif state["last_run"]:
            detail = f"última passada: {state['last_run']}"
        else:
            detail = "ainda sem passada (`nexo lineage agent install`)"
        out.append(Check("lineage", "linhagem", WARN if state["last_error"] or not state["last_run"] else OK, detail, False))
    return out


def _auth_checks(ctx: AppContext, installed: list[str]) -> list[Check]:
    from nexo.modules.execution.app.agents import adapters

    registry = adapters(ctx)
    out: list[Check] = []
    for name in installed:
        adapter = registry.get(EXE_TOOL.get(name, name))
        if adapter is None:
            continue
        auth = adapter.auth_status()
        verified = adapter.capabilities().verified
        status = OK if auth.authenticated else (WARN if auth.authenticated is None else FAIL)
        detail = auth.detail or ("autenticado" if auth.authenticated else "não autenticado")
        if auth.authenticated is False:
            detail += f" — rode `nexo login {name}`"  # login da ferramenta pelo Nexo
        if not verified:
            detail += " · contrato do adaptador não verificado"
        out.append(Check(f"auth:{name}", "agente", status, detail, False))
    return out


def _gitignore_check(ctx: AppContext) -> Check:
    """Pastas locais do Nexo fora do git (`.nexo/.gitignore`)."""
    from nexo.modules.setup.app.project import missing_gitignore

    root = ctx.project_root
    if root is None:
        return Check("gitignore", "projeto", OK, "fora de um projeto", False)
    missing = missing_gitignore(root)
    if not missing:
        return Check("gitignore", "projeto", OK, ".nexo/.gitignore completo", False)
    return Check("gitignore", "projeto", WARN, f"faltam {', '.join(missing)} (rode `nexo init` para completar)", False)


def _pricing_check(ctx: AppContext) -> Check:
    """custo calculado com preço de tabela pública (ou sem preço) é estimado até o cliente informar o contrato."""
    from nexo.modules.finops.app import pricing

    issues = pricing.check(ctx)
    if not issues:
        return Check("pricing", "finops", OK, "preços do contrato informados", False)
    items = ", ".join(i["item"] for i in issues)
    return Check("pricing", "finops", WARN, f"custo estimado para {items} (nexo cost pricing show)", False)


def _telemetry_check(ctx: AppContext) -> Check:
    """saúde da telemetria (pendências, atraso, registro do dispositivo, configurações ignoradas)."""
    from nexo.kernel.platform import telemetry

    data = telemetry.health(ctx)
    if not data["enabled"]:
        return Check("telemetry", "segurança", OK, "desligada pela org", False)
    level, days = telemetry.delay(ctx)
    status = FAIL if level == "block" else WARN if level == "warn" or not data["registered"] else OK
    detail = f"{data['contract']} · pendentes {data['pending']} (mais antiga há {days:.1f} dias)"
    if not data["registered"]:
        detail += " · dispositivo não registrado (`nexo login central`)"
    if data["ignored_settings"]:
        detail += f" · ignorado: {', '.join(data['ignored_settings'])}"
    return Check("telemetry", "segurança", status, detail, False)


def _isolation_check(ctx: AppContext) -> Check:
    """modo isolado — motor de contêiner e rede de saída quando exigido ou ligado."""
    from nexo.kernel.platform import isolation

    problems = isolation.check(ctx)
    return Check("isolation", "segurança", WARN if problems else OK, "; ".join(problems) or "ok ou não exigido", False)


def _guard_check(ctx: AppContext) -> Check:
    """guarda do Nexo no Claude Code do usuário (a org pode exigir com `guard.required`)."""
    from nexo.modules.setup.app import guard

    data = guard.status(ctx)
    installed = data["claude"]["installed"]
    status = OK if installed else (FAIL if data["required"] else WARN)
    detail = "instalada no Claude Code" if installed else "não instalada (nexo guard install)"
    return Check("guard", "segurança", status, detail + (" · exigida pela org" if data["required"] else ""), False)


def _hooks_check(ctx: AppContext) -> Check:
    """hooks de segurança sempre ativos + opcionais e tools conforme org/hooks.yaml e org/tools.yaml."""
    from nexo.modules.knowledge.app import content

    hooks = content.effective_hooks(ctx)
    off = [h for h, on in hooks["optional"].items() if not on]
    external = content.external_mcp(ctx)
    detail = (
        f"segurança: {', '.join(hooks['security'])}"
        + (f" · desligados pela org: {', '.join(off)}" if off else "")
        + ("" if hooks["handoff"] else " · handoff desligado")
        + (f" · MCPs externos: {', '.join(external)}" if external else "")
    )
    return Check("hooks", "segurança", OK, detail, False)


def _identity_check(ctx: AppContext) -> Check:
    """SEC-52: identidade usada para disparar e aprovar histórias."""
    from nexo.kernel.errors import NexoError
    from nexo.kernel.platform import identity

    try:
        who = identity.current(ctx)
    except NexoError as exc:
        return Check("identity", "segurança", WARN, exc.message, False)
    detail = who.id if who.verified else f"{who.id} (não verificada: liberado pela org)"
    return Check("identity", "segurança", OK if who.verified else WARN, detail, False)


def _writable(ctx: AppContext) -> bool:
    try:
        ctx.home.mkdir(parents=True, exist_ok=True)
        return os.access(ctx.home, os.W_OK)
    except OSError:
        return False


def problems(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c.required and c.status == FAIL]

"""Feature flags: toda funcionalidade do Nexo pode ser disponibilizada ou desligada remotamente, para a empresa
inteira ou por time (squad), sem release.

Precedência (a primeira definida vale): central por squad → central global → cérebro do squad → cérebro da org →
padrão do catálogo. O central tem efeito em minutos; o cérebro é a configuração versionada e assinada.

Fora do catálogo — **piso de segurança e governança, nunca desligável por flag**: política e hooks de segurança,
redação de segredos, trilha de auditoria, assinatura do cérebro, identidade verificada, portões de qualidade,
telemetria da org, kill switch, SIEM, limites de orçamento e modo isolado exigido.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Flag:
    key: str
    title: str
    area: str  # agrupamento para a tela de administração
    commands: tuple[str, ...] = ()  # comandos da CLI que dependem da flag (prefixo do caminho)
    default: bool = True
    where: str = "cli"  # cli | central | ambos
    stage: str = "ga"  # experimental | beta | ga | deprecated
    module: str = ""
    remove_after: str = ""
    squad_managed: bool = False


def _catalog() -> tuple[Flag, ...]:
    """O catálogo vem dos manifestos dos módulos: módulo novo traz as próprias flags, sem tabela central."""
    from nexo.kernel import modules

    return tuple(
        Flag(
            f.key,
            f.title,
            f.area or m.title,
            tuple(f.commands),
            f.default,
            f.where,
            f.stage,
            m.id,
            f.remove_after,
            f.squad_managed,
        )
        for m in modules.builtin()
        for f in m.flags
    )


CATALOG: tuple[Flag, ...] = _catalog()
BY_KEY = {f.key: f for f in CATALOG}
# comandos sempre disponíveis: gestão do próprio Nexo, segurança, governança e ação humana em portões pendentes
CORE_COMMANDS = frozenset(
    {
        "help",
        "version",
        "init",
        "doctor",
        "config",
        "login",
        "install",
        "update",
        "uninstall",
        "status",
        "logs",
        "resume",
        "approve",
        "reject",
        "telemetry",
        "killswitch",
        "flags",
        "brain",
        "isolation",
        "hook run",
        "mcp serve",
        "budget",
    }
)


@dataclass
class FlagSource:
    """Valores de uma fonte: `global_` (empresa) e por squad; None = não define."""

    name: str
    global_: dict[str, bool] = field(default_factory=dict)
    squads: dict[str, dict[str, bool]] = field(default_factory=dict)
    projects: dict[str, dict[str, bool]] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    key: str
    enabled: bool
    source: str  # central:squad | central | cérebro:squad | cérebro | padrão
    scope: str  # squad | global | padrão


def validate(data: dict[str, Any], where: str) -> list[str]:
    """Chaves desconhecidas ou valores não booleanos (cérebro e central)."""
    problems = []
    for key, value in data.items():
        if key not in BY_KEY:
            problems.append(f"{where}: flag desconhecida {key}")
        elif not isinstance(value, bool):
            problems.append(f"{where}: {key} deve ser true/false")
    return problems


def resolve(
    key: str, squad: str, sources: list[FlagSource], project: str = "", local_off: frozenset[str] = frozenset()
) -> Decision:
    """Precedência: central (projeto, time, global) → cérebro (projeto, time, global) → padrão. Local só desliga."""
    if key not in BY_KEY:
        raise KeyError(f"flag desconhecida: {key}")
    decision = Decision(key, BY_KEY[key].default, "padrão", "padrão")
    for src in sources:  # ordem: central, cérebro
        by_project = src.projects.get(project, {}) if project else {}
        by_squad = src.squads.get(squad, {}) if squad else {}
        if key in by_project:
            decision = Decision(key, bool(by_project[key]), f"{src.name}:projeto", "projeto")
            break
        if key in by_squad:
            decision = Decision(key, bool(by_squad[key]), f"{src.name}:squad", "squad")
            break
        if key in src.global_:
            decision = Decision(key, bool(src.global_[key]), src.name, "global")
            break
    if decision.enabled and key in local_off:  # local só desliga
        return Decision(key, False, "local", "local")
    return decision


# comandos fundidos — o caminho novo é governado pela mesma flag do caminho antigo
MERGED = {
    ("cost", "usage"): "usage",
    ("cost", "budget"): "budget",
    ("cost", "pricing"): "pricing",
    ("cost", "estimate"): "estimate",
    ("metrics", "baseline"): "baseline",
    ("quality", "standards"): "standards",
    ("quality", "perf"): "perf",
    ("audit", "evidence"): "evidence",
}
GROUP_PREFIXES = ("admin", "extensions")  # `admin flags` → `flags`; `extensions skill` → `skill`


def canonical(words: list[str]) -> list[str]:
    """Caminho canônico (o antigo) de um comando, para a flag e a auditoria valerem igual nos dois nomes."""
    if words and words[0] in GROUP_PREFIXES:
        return words[1:]
    if len(words) >= 2 and (words[0], words[1]) in MERGED:
        return [MERGED[(words[0], words[1])], *words[2:]]
    if words[:1] == ["inbox"]:
        return ["plan", "inbox"]
    return words


def flag_for_command(path: list[str]) -> Flag | None:
    """Flag que governa o comando (maior prefixo); comandos centrais nunca são bloqueados."""
    words = canonical([w for w in path if not w.startswith("-")])
    for size in (2, 1):  # prefixo mais longo primeiro (ex.: "budget watch" antes de "budget")
        prefix = " ".join(words[:size])
        flag = next((f for f in CATALOG if prefix in f.commands), None)
        if flag is not None:
            return flag
        if prefix in CORE_COMMANDS:
            return None
    return None

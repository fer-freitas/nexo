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


CATALOG: tuple[Flag, ...] = (
    # planejamento e conhecimento
    Flag("planning", "Planejamento com agente (ingestão, refinamento, backlog)", "Planejamento", ("plan",)),
    Flag("templates", "Templates de artefatos e documentos", "Planejamento", ("template",)),
    Flag(
        "discovery",
        "Discovery de produto do PM (hipóteses, evidências e decisão)",
        "Planejamento",
        ("plan discovery", "plan decide"),
    ),
    Flag(
        "role_flow",
        "Fluxo por papéis: sessão conjunta, pacote e aprovação de cada papel",
        "Planejamento",
        ("plan session", "plan package", "plan reject", "plan inbox", "plan roles"),
    ),
    Flag("discover", "Descoberta de código existente (mapa, baseline, as-is)", "Planejamento", ("discover",)),
    # execução
    Flag("run_task", "Execução de tarefa por agente", "Execução", ("run task",)),
    Flag("run_story", "Execução de história ponta a ponta", "Execução", ("run story",)),
    Flag("run_feature", "Feature inteira desassistida", "Execução", ("run feature",)),
    Flag("runner", "Runner remoto por fila", "Execução", ("runner",)),
    Flag("handoff", "Troca automática de rota quando a cota acaba ou a rota falha", "Execução"),
    Flag("watch", "Retomada automática (nexo cost budget watch)", "Execução", ("budget watch",)),
    Flag("pr_publish", "Push da branch e abertura de PR das histórias", "Execução"),
    Flag("pr_reaction", "Reação ao CI e aos pedidos de mudança no PR", "Execução", ("pr sync",)),
    Flag("auto_merge", "Merge automático no nível de autonomia L3", "Execução"),
    Flag("autonomy", "Autonomia conquistada (consulta e pedido de mudança)", "Execução", ("autonomy",)),
    Flag("clarification_teams", "Perguntas do agente pelo Teams com resposta pelo central", "Execução"),
    Flag("approvals_teams", "Aprovação dos portões pelo Teams (formulário no central)", "Execução"),
    Flag("approvals_external", "Aprovação externa (ITSM/HTTP)", "Execução", ("approvals",)),
    # integrações e qualidade
    Flag("ask", "Perguntas sobre o código e o cérebro", "Conhecimento", ("ask",)),
    Flag("ready", "Definition of Ready e teste de performance pelo refinamento", "Planejamento", ("ready", "perf")),
    Flag("writer", "Agente writer: documentação viva e conhecimento no cérebro", "Conhecimento", ("docs",)),
    Flag("graph", "Índice em grafo do código e do cérebro (impacto e contexto)", "Conhecimento", ("graph",)),
    Flag("guard", "Guarda do Nexo nas sessões de IA fora do Nexo", "Segurança", ("guard install",)),
    Flag("pr_review", "Revisão de PRs feitos por humanos", "Qualidade", ("review",)),
    Flag("evidence", "Pacote de evidências por release", "Qualidade", ("evidence",)),
    Flag("tracker_sync", "Sincronização com o tracker (Azure Boards, GitHub, Jira)", "Integrações", ("tracker",)),
    Flag("quality_cli", "Pipeline de qualidade avulso e geração de CI", "Qualidade", ("quality",)),
    Flag(
        "extensions",
        "Skills, hooks e tools de extensão (instalar e gerenciar)",
        "Qualidade",
        ("skill", "tool", "hook add", "hook remove", "hook list"),
    ),
    Flag("eval", "Avaliação de rotas, skills e portão de regressão", "Qualidade", ("eval",)),
    Flag("monitor", "Monitor sintético das CLIs", "Qualidade", ("monitor",)),
    # conhecimento, custo e métricas
    Flag("learning", "Aprendizado contínuo (lições e curadoria)", "Conhecimento", ("learn",)),
    Flag("finops_reports", "Relatórios de custo, estimativa e fechamento", "FinOps", ("cost", "estimate")),
    Flag("metrics", "Métricas locais e linha de base", "FinOps", ("metrics", "baseline")),
    Flag("external_usage", "Consumo de IA fora do Nexo (leitura local e envio agregado)", "FinOps", ("usage",)),
    Flag("gates_calibrate", "Calibração dos limiares dos portões", "FinOps", ("gates",)),
    Flag("insights", "Insights do central (motor e consulta)", "Insights", ("insights",), where="ambos"),
    # observabilidade
    Flag("traces", "Traces OpenTelemetry para o APM", "Observabilidade"),
    Flag("transcripts", "Replay redigido das sessões do agente", "Observabilidade"),
    # central
    Flag("dashboard", "Dashboard unificado", "Central", where="central"),
    Flag("weekly_summary", "Resumo semanal por squad no Teams", "Central", where="central"),
    Flag("finops_report", "Relatório mensal de FinOps", "Central", where="central"),
    Flag("systemic_issues", "Issues sistêmicas no nexo-brain", "Central", where="central"),
    Flag("realtime_alerts", "Alertas de custo e loop em tempo real", "Central", where="central"),
    Flag("bi_export", "Export para BI (CSV/Parquet)", "Central", where="central"),
)
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


def resolve(key: str, squad: str, sources: list[FlagSource]) -> Decision:
    if key not in BY_KEY:
        raise KeyError(f"flag desconhecida: {key}")
    for src in sources:  # ordem: central, cérebro
        by_squad = src.squads.get(squad, {}) if squad else {}
        if key in by_squad:
            return Decision(key, bool(by_squad[key]), f"{src.name}:squad", "squad")
        if key in src.global_:
            return Decision(key, bool(src.global_[key]), src.name, "global")
    return Decision(key, BY_KEY[key].default, "padrão", "padrão")


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

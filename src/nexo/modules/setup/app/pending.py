"""Console do Nexo: o que está pendente agora e o resumo do projeto, lidos do estado local, sem IA.

Usado pela sessão interativa (`nexo` sem comando): a pessoa vê os próximos passos numerados e escolhe pelo número.
Nada aqui grava auditoria nem chama ferramenta externa: é rápido e não custa nada.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.platform import config

STORY_ORDER = {"AguardandoHumano": 0, "Escalado": 1, "Bloqueado": 2, "Pronto": 3, "Refinamento": 4}


@dataclass(frozen=True)
class Step:
    label: str  # "US-1-2 aguarda sua aprovação"
    argv: tuple[str, ...]  # comando que resolve
    detail: str = ""  # motivo, custo, dúvidas...
    item: str = ""  # id em foco ao escolher
    level: str = "info"  # decide (espera você) | problem (parou) | info (próximo passo comum)
    needs: str = ""  # argumento que falta e o console pergunta antes de rodar (ex.: "<F-n>")


@dataclass(frozen=True)
class Header:
    project: str
    stacks: list[str]
    features: int
    stories: dict[str, int]  # estado → quantidade
    month_brl: float | None
    tracker: str
    scm: str
    who: str = ""
    roles: tuple[str, ...] = ()


def header(ctx: AppContext) -> Header | None:
    root = ctx.project_root
    if root is None:
        return None
    from nexo.store.runs import project_key

    cfg = config.resolved(ctx).values
    states: dict[str, int] = {}
    features = 0
    for row in ctx.conn.execute(
        "SELECT type, state FROM work_items WHERE project = ? AND type IN ('feature', 'story')", (project_key(root),)
    ):
        if row["type"] == "feature":
            features += 1
        else:
            states[str(row["state"])] = states.get(str(row["state"]), 0) + 1
    month: float | None
    try:
        from nexo.kernel import services

        costs = services.load("finops.costs")

        month = costs.budget_status(ctx).spent_brl  # seu gasto no mês (dentro do Nexo)
    except Exception:  # custo é só informativo no cabeçalho
        month = None
    who, roles = _who(ctx)
    return Header(
        who=who,
        roles=roles,
        project=str(cfg.get("project") or root.name),
        stacks=[str(s) for s in cfg.get("stacks") or []],
        features=features,
        stories=states,
        month_brl=month,
        tracker=str((cfg.get("tracker") or {}).get("kind", "local")),
        scm=str((cfg.get("scm") or {}).get("provider") or ""),
    )


def _who(ctx: AppContext) -> tuple[str, tuple[str, ...]]:
    try:
        from nexo.kernel import services
        from nexo.kernel.platform import identity

        roles_of = services.get("planning.roleflow", "roles_of")

        who = identity.current(ctx).id
        return who, tuple(roles_of(ctx, who))
    except Exception:  # identidade e papéis são só informativos no cabeçalho
        return "", ()


def next_steps(ctx: AppContext, limit: int = 9) -> list[Step]:
    """Ações que destravam o trabalho agora, da mais urgente para a menos (portões humanos primeiro)."""
    root = ctx.project_root
    if root is None:
        return [Step("preparar esta pasta para o Nexo", ("init",), "cria .nexo/ e detecta a stack")]
    from nexo.store.runs import RunStore, project_key

    store = RunStore(ctx.conn, project_key(root))
    steps: list[Step] = _demand_steps(ctx)
    stories = [
        (str(r["id"]), str(r["title"]), str(r["state"]))
        for r in ctx.conn.execute(
            "SELECT id, title, state FROM work_items WHERE project = ? AND type = 'story' ORDER BY id",
            (project_key(root),),
        )
    ]
    for sid, title, state in sorted(stories, key=lambda s: (STORY_ORDER.get(s[2], 9), s[0])):
        run = store.latest_run(sid)
        reason = (run.reason or "") if run else ""
        if state == "AguardandoHumano":
            steps.append(Step(f"{sid} aguarda sua aprovação", ("approve", sid), _short(reason or title), sid, "decide"))
        elif state in ("Escalado", "Bloqueado"):
            what = "escalou" if state == "Escalado" else "está bloqueada"
            steps.append(Step(f"{sid} {what}: ver o motivo e retomar", ("resume", sid), _short(reason), sid, "problem"))
        elif state == "Refinamento" and run is not None and (agent := _agent_step(root, sid)) is not None:
            steps.append(agent)  # parou por pergunta do agente durante a execução
        elif state in ("Pronto", "Refinamento"):
            steps.append(_story_ready_step(ctx, root, sid, title))
    steps = (
        _role_steps(ctx)
        + _dev_unclear_steps(root)
        + steps
        + _feature_steps(ctx, root, store)
        + _delivery_steps(ctx, root, store)
        + _classify_steps(root)
        + _tracker_file_steps(ctx, root)
        + _tracker_export_steps(ctx, root)
        + _tracker_map_steps(ctx)
        + _portal_steps(ctx, root, store)
        + _autopilot_steps(root)
    )
    context = _context_step(ctx, root)
    later = _docs_step(ctx, root) + _setup_steps(ctx, root) + _finops_steps(ctx)  # depois da ação principal
    if not steps and not stories:
        steps += [
            *context,
            Step("planejar uma feature nova", ("plan",), "descreva a ideia ou indique o arquivo do pedido (pedido.md)"),
            *later,
        ]
    else:
        steps += context + later
    return steps[:limit]


def _demand_steps(ctx: AppContext) -> list[Step]:
    """Quem está com a demanda sai dos fatos: o que é seu aparece primeiro."""
    from nexo.kernel import services
    from nexo.kernel.errors import NexoError

    if not services.available("demands.core"):
        return []
    try:
        rows = services.load("demands.core").holder_inbox(ctx)
    except (NexoError, OSError):
        return []
    return [
        Step(
            f"{r['demand']}: {r['action']}",
            ("brief", str(r["demand"])),
            str(r["title"])[:70],
            str(r["demand"]),
            "decide",
        )
        for r in rows[:5]
    ]


def _dev_unclear_steps(root: Path) -> list[Step]:
    """A explicação da tarefa achou uma dúvida real: levar ao tech lead (ou seguir com o que já está decidido)."""
    from nexo.kernel import services

    devflow = services.load("execution.devflow")

    return [
        Step(
            f"{item}: levar {n} dúvida{'s' if n > 1 else ''} ao tech lead",
            ("explain", item, "--ask"),
            fid,
            item,
            "decide",
        )
        for item, fid, n in devflow.pending_unclear(root)
    ]


def _agent_step(root: Path, sid: str) -> Step | None:
    """A história parou porque o agente perguntou algo: responder; respondida, retomar dali."""
    from nexo.kernel import services

    clarification = services.load("execution.story_clarification")
    from nexo.kernel import services

    feature_for_story = services.get("planning.clarify", "feature_for_story")

    folder = feature_for_story(root, sid)
    if folder is None or not clarification.agent_questions(folder, sid):
        return None
    fid = "-".join(folder.name.split("-")[:2])
    pending = clarification.open_agent_questions(folder, sid)
    if pending:
        return Step(
            f"{sid}: responder a pergunta do agente", ("plan", "answer", fid), _short(pending[0].text), sid, "decide"
        )
    ok, missing = clarification.resumable(folder, sid)
    if ok:
        return Step(
            f"{sid}: retomar (pergunta respondida)", ("resume", sid), "a execução continua de onde parou", sid, "decide"
        )
    return Step(f"{sid}: aprovar o plano de novo", ("plan", "approve", fid), _short(missing), sid, "decide")


def _docs_step(ctx: AppContext, root: Path) -> list[Step]:
    """Plano aprovado e nenhum destino de documentação escolhido: onde fica a base de conhecimento?"""
    from nexo.kernel import services
    from nexo.kernel.workspace import specs

    docs = services.load("knowledge.docs")

    origin = config.resolved(ctx).origin.get("docs.publish.target", "default")
    if docs.target_kind(ctx) != "none" or origin != "default":  # alguém já escolheu (inclusive "none")
        return []
    base = specs.specs_root(root)
    if not base.is_dir() or not any((f / "backlog.yaml").is_file() for f in base.glob("F-*")):
        return []
    detail = "Docusaurus (GitHub Pages), Confluence, Azure Wiki ou só no repositório"
    return [Step("escolher onde fica a documentação", ("docs", "target"), detail)]


def _context_step(ctx: AppContext, root: Path) -> list[Step]:
    """projeto sem contexto — a IA planeja melhor sabendo o produto, a parte e o que o repositório faz."""
    if (root / ".nexo" / "contexto.md").is_file():
        return []
    return [Step("contar ao Nexo sobre este projeto", ("context", "init"), "produto, parte do produto e o que ele faz")]


def _autopilot_steps(root: Path) -> list[Step]:
    """Avisos do piloto automático (vulnerabilidade, calibração do portão): o que pede decisão humana."""
    from nexo.modules.setup.app import autopilot

    return [
        Step(
            a.label,
            tuple(a.command.split()),
            a.detail,
            level="problem" if a.command.startswith(("deps", "audit")) else "decide",
        )
        for a in autopilot.alerts(root)
    ]


def _role_steps(ctx: AppContext) -> list[Step]:
    """O que espera pelo seu papel no fluxo por papéis (decisão de discovery do PM, partes do plano do PM e do tech
    lead, sessão conjunta) — o mesmo que o antigo `plan inbox`."""
    try:
        from nexo.kernel import services

        roleflow = services.load("planning.roleflow")
        from nexo.kernel import services

        Planner = services.get("planning.plan", "Planner")

        items = roleflow.inbox(Planner(ctx, None, lambda _k, _d: None))["items"]
    except Exception:  # fluxo por papéis não configurado: nada a mostrar
        return []
    out = []
    for item in items:
        argv = tuple(str(item["command"]).removeprefix("nexo ").split())
        detail = ""
        if "--part" in argv:  # aprovar uma parte do plano: a alternativa é recusar com o motivo
            part = argv[argv.index("--part") + 1]
            detail = f'ou recusar: plan reject {item["feature"]} --part {part} --reason "..."'
        out.append(Step(f"{item['feature']}: {item['action']}", argv, detail, str(item["feature"]), "decide"))
    return out


def _delivery_steps(ctx: AppContext, root: Path, store: Any) -> list[Step]:
    """Feature com todas as histórias concluídas: pacote de evidências e, com ITSM configurado, o pedido de mudança."""
    from nexo.kernel.workspace import specs

    base = specs.specs_root(root)
    if not base.is_dir():
        return []
    channel = str((config.resolved(ctx).values.get("change") or {}).get("channel") or "")
    out: list[Step] = []
    for folder in sorted(base.glob("F-*")):
        backlog = specs.load_backlog(folder) if (folder / "backlog.yaml").is_file() else None
        if backlog is None or not backlog.stories:
            continue
        if any((store.item_state(s.id) or "") != "Concluido" for s in backlog.stories):
            continue
        fid = "-".join(folder.name.split("-")[:2])
        if not any((root / ".nexo" / "evidencias").glob(f"{fid}-*")):
            out.append(
                Step(
                    f"{fid}: montar o pacote de evidências",
                    ("audit", "evidence", "build", fid),
                    "todas as histórias concluídas: dossiê assinado para auditoria",
                    fid,
                    "decide",
                )
            )
        elif channel and not (folder / "change.yaml").is_file():
            out.append(
                Step(
                    f"{fid}: abrir o pedido de mudança",
                    ("change", "open", fid),
                    f"requisição no {channel} a partir das evidências",
                    fid,
                    "decide",
                )
            )
    return out


def _tracker_file_steps(ctx: AppContext, root: Path) -> list[Step]:
    """Tracker por arquivo (CSV com a ferramenta de gestão): itens novos no outbox ainda não trazidos de volta."""
    cfg = dict(config.resolved(ctx).values.get("tracker") or {})
    if cfg.get("kind") != "file":
        return []
    folder = root / str((cfg.get("file") or {}).get("dir") or ".nexo/tracker")
    outbox, inbox = folder / "outbox.csv", folder / "inbox.csv"
    if not outbox.is_file() or (inbox.is_file() and inbox.stat().st_mtime >= outbox.stat().st_mtime):
        return []
    rel = outbox.relative_to(root).as_posix() if outbox.is_relative_to(root) else str(outbox)
    return [
        Step(
            "tracker por arquivo: importar o outbox e trazer os estados de volta",
            ("tracker", "import"),
            f"importe {rel} na ferramenta de gestão e indique aqui o CSV exportado de lá",
            "",
            "info",
            "<arquivo.csv>",
        )
    ]


def _classify_steps(root: Path) -> list[Step]:
    """Feature em refinamento sem classe de dados decidida: o que pode ir para a IA ao construir."""
    from nexo.kernel.workspace import specs

    base = specs.specs_root(root)
    if not base.is_dir():
        return []
    out: list[Step] = []
    for folder in sorted(base.glob("F-*")):
        if not (folder / "spec.yaml").is_file() or (folder / "backlog.yaml").is_file():
            continue  # sem spec ou já aprovada: a classe do projeto vale
        try:
            data_class = specs.load_spec(folder).data_class
        except Exception:  # spec em edição: não atrapalha o console
            data_class = "?"
        if not data_class:
            fid = "-".join(folder.name.split("-")[:2])
            detail = "public, internal, confidential ou restricted: o que pode ir para a IA ao construir"
            out.append(
                Step(f"{fid}: decidir a classe de dados", ("plan", "classify", fid), detail, fid, "info", "<classe>")
            )
    return out


def _tracker_export_steps(ctx: AppContext, root: Path) -> list[Step]:
    """Tracker local com histórias aprovadas e nada exportado: levar à ferramenta de gestão por CSV."""
    from nexo.kernel.workspace import specs

    if str((config.resolved(ctx).values.get("tracker") or {}).get("kind") or "local") != "local":
        return []
    base = specs.specs_root(root)
    if not base.is_dir() or not any(base.glob("F-*/backlog.yaml")):
        return []
    if (root / ".nexo" / "tracker" / "outbox.csv").is_file():
        return []
    detail = "CSV para importar na ferramenta de gestão (ou tracker.kind file para gerar sozinho)"
    return [Step("exportar as histórias para a ferramenta de gestão", ("tracker", "export"), detail)]


def _tracker_map_steps(ctx: AppContext) -> list[Step]:
    """De-para com o tracker sem algum estado ou tipo do Nexo (conferido sem chamar o tracker)."""
    kind = str((config.resolved(ctx).values.get("tracker") or {}).get("kind") or "local")
    if kind not in ("file", "github"):
        return []
    try:
        from nexo.kernel import services
        from nexo.kernel.tracker.mapping import check

        tracker = services.load("demands.tracker")

        problems = check(tracker.load_mapping(ctx, kind), None)
    except Exception:  # de-para ilegível: o `tracker map --check` explica
        problems = ["de-para ilegível"]
    if not problems:
        return []
    return [Step("de-para com o tracker incompleto", ("tracker", "map", "--check"), _short(problems[0]), "", "problem")]


def _finops_steps(ctx: AppContext) -> list[Step]:
    """Custo estimado (preço de tabela) e mês sem orçamento: o que o FinOps e o gestor ainda não informaram."""
    out: list[Step] = []
    try:
        from nexo.kernel import services

        costs = services.load("finops.costs")
        pricing = services.load("finops.pricing")

        issues = pricing.check(ctx)
        if issues:
            detail = f"{len(issues)} preço(s) de tabela ou sem preço: o custo aparece como estimado"
            out.append(Step("informar os preços do contrato", ("cost", "pricing", "check"), detail))
        budget = costs.budget_status(ctx)
        if budget.limit_brl is None and budget.spent_brl > 0:
            detail = f"R$ {budget.spent_brl:.2f} gastos no mês sem limite definido"
            out.append(Step("definir o orçamento do mês", ("cost", "budget", "set", "--limit"), detail, needs="<R$>"))
    except Exception:  # custo é informativo: nunca derruba os próximos passos
        return out
    return out


def _setup_steps(ctx: AppContext, root: Path) -> list[Step]:
    """Projeto recém-preparado: mapa do código existente e linha de base das métricas."""
    out: list[Step] = []
    has_code = any(
        p.suffix in {".py", ".cs", ".java", ".ts", ".tsx", ".js"} for p in root.glob("src/**/*") if p.is_file()
    )
    if has_code and not (root / ".nexo" / "map").is_dir():
        out.append(Step("mapear o código existente", ("discover",), "módulos, convenções e baseline do que já falha"))
    tracker = str((config.resolved(ctx).values.get("tracker") or {}).get("kind") or "local")
    if tracker not in ("local", "file") and not (root / ".nexo" / "metrics" / "baseline.json").is_file():
        out.append(
            Step("medir a linha de base antes do Nexo", ("metrics", "baseline"), "lead time, vazão e bugs pelo tracker")
        )
    return out


def inbox(ctx: AppContext) -> dict[str, Any]:
    """`nexo inbox`: quem é você, seus papéis e o que espera por você (a mesma lista do console)."""
    from nexo.kernel.platform import identity

    who = identity.current(ctx)
    try:
        from nexo.kernel import services

        roles_of = services.get("planning.roleflow", "roles_of")

        roles = list(roles_of(ctx, who.id))
    except Exception:
        roles = []
    steps = next_steps(ctx, limit=50)
    return {
        "who": who.id,
        "roles": roles,
        "items": [
            {"label": s.label, "command": " ".join(s.argv), "detail": s.detail, "item": s.item, "level": s.level}
            for s in steps
        ],
    }


def _story_ready_step(ctx: AppContext, root: Path, sid: str, title: str) -> Step:
    from nexo.kernel import ready as ready_domain
    from nexo.kernel import services
    from nexo.kernel.runtime import yamlfile

    ready = services.load("planning.ready")

    mode = ready.mode(ctx)
    try:
        folder, acs, kinds = ready._story(root, sid)
    except Exception:  # história sem backlog local (veio do tracker): só executar
        return Step(f"{sid} pronta para executar", ("run", "story", sid), _short(title), sid)
    path = ready.refinement_path(folder, sid)
    if mode != "off" and not path.is_file():
        return Step(f"{sid}: preparar a Definition of Ready", ("ready", sid, "--init"), _short(title), sid)
    if mode != "off":
        data: dict[str, Any] = dict(yamlfile.load(path))
        rep = ready_domain.check(data, sid, acs, kinds, ready.floors(ctx))
        if not rep.ready:
            rel = path.relative_to(root).as_posix()
            detail = _short(f"{'; '.join(rep.problems)} · {rel}")
            return Step(f"{sid}: completar a Definition of Ready", ("ready", sid), detail, sid)
    return Step(f"{sid} pronta para executar", ("run", "story", sid), _short(title), sid)


def _feature_steps(ctx: AppContext, root: Path, store: Any) -> list[Step]:
    from nexo.kernel.spec.questions import open_questions
    from nexo.kernel.workspace import specs

    out: list[Step] = []
    base = specs.specs_root(root)
    for folder in sorted(base.glob("F-*")) if base.is_dir() else []:
        if not folder.is_dir():
            continue
        fid = "-".join(folder.name.split("-")[:2])
        state = store.item_state(fid) or ""
        if state not in ("Ideia", "Refinamento"):
            continue
        pending = open_questions(specs.load_questions(folder))
        if pending:
            n = len(pending)
            out.append(
                Step(
                    f"{fid} tem {n} dúvida{'s' if n > 1 else ''} aberta{'s' if n > 1 else ''}",
                    ("plan", "answer", fid),
                    _short(pending[0].text),
                    fid,
                )
            )
        elif specs.load_backlog(folder) is None:
            out += _tech_steps(ctx, folder, fid)
            out.append(Step(f"{fid}: aprovar o plano e quebrar em histórias", ("plan", "approve", fid), "", fid))
    return out


def _is_tech_lead(ctx: AppContext) -> bool:
    """Sem fluxo por papéis, a pessoa tem todos os papéis."""
    try:
        from nexo.kernel import services

        roleflow = services.load("planning.roleflow")

        return not roleflow.active(ctx) or "tech_lead" in _who(ctx)[1]
    except Exception:  # fluxo por papéis mal configurado não esconde os passos
        return True


def _portal_steps(ctx: AppContext, root: Path, store: Any) -> list[Step]:
    """o que o portal do produto apontaria neste repositório, para o tech lead resolver aqui mesmo:
    feature com história entregue e sem requisito técnico, e o código usando outra parte sem declarar."""
    from nexo.kernel.workspace import specs

    if not _is_tech_lead(ctx):
        return []
    out: list[Step] = []
    base = specs.specs_root(root)
    for folder in sorted(base.glob("F-*")) if base.is_dir() else []:
        backlog = specs.load_backlog(folder) if folder.is_dir() else None
        if backlog is None or (folder / "requisito-tecnico.md").is_file():
            continue
        done = [s.id for s in backlog.stories if store.item_state(s.id) == "Concluido"]
        if done:
            fid = "-".join(folder.name.split("-")[:2])
            detail = f"{', '.join(done[:3])} já entregue(s); o portal do produto aponta essa falta"
            out.append(Step(f"{fid}: escrever o requisito técnico", ("tech", "spec", fid), detail, fid, "problem"))
    return out


def _tech_steps(ctx: AppContext, folder: Path, fid: str) -> list[Step]:
    """o que é do tech lead antes da aprovação do plano (quem trabalha sozinho tem todos os papéis)."""
    if not _is_tech_lead(ctx):
        return []
    if not (folder / "analise-tecnica.md").is_file():
        detail = "lacunas, riscos e perguntas para o PM antes do requisito técnico"
        return [Step(f"{fid}: analisar o requisito (tech lead)", ("tech", "analyze", fid), detail, fid)]
    if not (folder / "requisito-tecnico.md").is_file():
        gaps = _functional_gaps(folder)
        if gaps and not _returned(folder):  # a análise achou lacuna funcional: a decisão vira duas opções
            first = _short(gaps[0], 70)
            return [
                Step(f"{fid}: assumir a lacuna funcional como premissa", ("tech", "spec", fid), first, fid, "decide"),
                Step(f"{fid}: devolver a lacuna funcional ao PM", ("tech", "return", fid), first, fid, "decide"),
            ]
        detail = "design, decisões e premissas — opcional em feature simples"
        return [Step(f"{fid}: escrever o requisito técnico (tech lead)", ("tech", "spec", fid), detail, fid)]
    return []


def _functional_gaps(folder: Path) -> list[str]:
    from nexo.kernel.runtime import yamlfile

    path = folder / "analise-tecnica.yaml"
    data = yamlfile.load(path) if path.is_file() else {}
    return [str(g.get("text", "")) for g in data.get("functional_gaps") or [] if isinstance(g, dict)]


def _returned(folder: Path) -> bool:
    from nexo.kernel.workspace import specs

    return any(x.source == "tech_lead" for x in specs.load_questions(folder))


def _short(text: str, size: int = 90) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= size else text[: size - 1] + "…"


def story_state(ctx: AppContext, story_id: str) -> str:
    root = ctx.project_root
    if root is None:
        return ""
    from nexo.store.runs import project_key

    row = ctx.conn.execute(
        "SELECT state FROM work_items WHERE project = ? AND id = ?", (project_key(root), story_id)
    ).fetchone()
    return str(row["state"]) if row else ""


def candidates(ctx: AppContext, slot: str) -> list[str]:
    """Ids em aberto para a pergunta "qual história?" / "qual feature?"."""
    root = ctx.project_root
    if root is None or slot not in ("story", "feature", "item"):
        return []
    from nexo.store.runs import project_key

    kinds = {"story": ("story",), "feature": ("feature",), "item": ("story", "feature")}[slot]
    rows = ctx.conn.execute(
        f"SELECT id FROM work_items WHERE project = ? AND type IN ({','.join('?' * len(kinds))}) "  # noqa: S608
        "AND state NOT IN ('Concluido', 'Cancelado') ORDER BY id",
        (project_key(root), *kinds),
    ).fetchall()
    return [str(r["id"]) for r in rows]

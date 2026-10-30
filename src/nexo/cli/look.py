"""Linguagem visual do console: cabeçalho, próximos passos, ajuda e o andamento da história em fases.

Cores só pelos estilos `nexo.*` (vêm da marca, ver `resources/brand.yaml`); dinheiro sempre em `R$ 1.234,56`.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.status import Status
from rich.table import Table
from rich.text import Text

from nexo.kernel.finops.money import brl

REDACTION_HINT = (
    "  [nexo.muted]dado pessoal ou segredo dos insumos fica oculto para a IA; em exemplos, use domínios de "
    "exemplo (ex.: joao@exemplo.com), que ficam visíveis[/]"
)
IN_CONSOLE = {"on": False}  # dentro do console os comandos aparecem sem o prefixo `nexo`
ACTIVE: dict[str, StoryProgress] = {}  # andamento da história em curso (um por comando)

STATE_LABEL = {
    "AguardandoHumano": ("aguardando você", "aguardando você"),
    "Escalado": ("escalada", "escaladas"),
    "Bloqueado": ("bloqueada", "bloqueadas"),
    "EmDev": ("em andamento", "em andamento"),
    "EmQA": ("em teste", "em teste"),
    "Pronto": ("pronta", "prontas"),
    "Refinamento": ("em refinamento", "em refinamento"),
    "Ideia": ("ideia", "ideias"),
    "Concluido": ("concluída", "concluídas"),
    "Cancelado": ("cancelada", "canceladas"),
}
STATE_STYLE = {
    "AguardandoHumano": "nexo.warn",
    "Escalado": "nexo.bad",
    "Bloqueado": "nexo.bad",
    "Concluido": "nexo.good",
}
LEVEL_STYLE = {"decide": "nexo.warn", "problem": "nexo.bad", "info": "nexo.accent"}
PHASES = {
    "qa": "QA",
    "characterize": "Caracterização",
    "tasks": "Dev",
    "quality": "Qualidade",
    "bugfix": "Correção",
}
PHASE_DOING = {
    "qa": "escrevendo os testes de aceite",
    "characterize": "travando o comportamento atual",
    "tasks": "implementando as tarefas",
    "quality": "lint, testes, segurança e revisão",
    "bugfix": "corrigindo o que falhou",
}


def cmd(text: str) -> str:
    """Comando para a pessoa digitar: sem `nexo` dentro do console."""
    return text if IN_CONSOLE["on"] else f"nexo {text}"


def state_label(state: str, n: int = 1) -> str:
    one, many = STATE_LABEL.get(state, (state, state))
    return one if n == 1 else many


def duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return (
        f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
        if seconds >= 3600
        else (f"{seconds // 60}:{seconds % 60:02d}")
    )


# ---------------------------------------------------------------------- cabeçalho e passos


def header(console: Console, head: Any, brand_name: str = "Nexo") -> None:
    """Uma linha com o projeto e o estado, outra com quem você é e como usar."""
    line = Text()
    line.append(brand_name.lower(), style="nexo.brand")
    line.append(" │ ", style="nexo.muted")
    line.append(head.project, style="nexo.title")
    if head.stacks:
        line.append(f" · {', '.join(head.stacks)}", style="nexo.muted")
    order = list(STATE_LABEL)
    states = sorted(head.stories.items(), key=lambda kv: order.index(kv[0]) if kv[0] in order else 99)
    for state, n in states:
        line.append("  ")
        line.append(f" {n} {state_label(state, n)} ", style=f"reverse {STATE_STYLE.get(state, 'nexo.accent')}")
    if not states:
        line.append("  nenhuma história ainda", style="nexo.muted")
    if head.month_brl is not None:
        line.append(" · ", style="nexo.muted")
        line.append(f"{brl(head.month_brl)} no mês")
    console.print(line)
    who = Text(style="nexo.muted")
    if head.who:
        from nexo.kernel.roles import ROLE_LABEL

        roles = [ROLE_LABEL.get(r, r) for r in head.roles]
        papel = ", ".join(roles[:-1]) + f" e {roles[-1]}" if len(roles) > 1 else (roles[0] if roles else "")
        who.append(f"você: {head.who}" + (f" · {papel}" if papel else "") + " · ")
    tracker = f"tracker {head.tracker}" + (f" · {head.scm}" if head.scm not in ("", "none") else "")
    who.append(f"{tracker} · um número, uma frase do seu jeito, ? próximos passos, ajuda, sair")
    console.print(who)


def steps(console: Console, items: Iterable[Any], title: str = "Para agora") -> None:
    rows = list(items)
    console.print()
    console.print(Text(title, style="nexo.title"))
    grid = Table.grid(padding=(0, 2), expand=True)
    grid.add_column(width=3, no_wrap=True, min_width=3)
    grid.add_column(width=2, justify="right", no_wrap=True, min_width=2)
    grid.add_column(no_wrap=True, overflow="ellipsis", max_width=52)
    grid.add_column(no_wrap=True, overflow="ellipsis", style="nexo.muted", ratio=1)
    for i, step in enumerate(rows, start=1):
        level = getattr(step, "level", "info")
        grid.add_row(
            Text("  ▌", style=LEVEL_STYLE.get(level, "nexo.accent")),
            Text(str(i), style="nexo.key"),
            Text(step.label),
            Text(step.detail or ""),
        )
    console.print(grid, crop=True)
    console.print()


def help_table(console: Console, entries: Iterable[tuple[str, str]], title: str) -> None:
    console.print()
    console.print(Text(title, style="nexo.title"))
    grid = Table.grid(padding=(0, 2))
    grid.add_column(no_wrap=True, style="nexo.accent")
    grid.add_column(style="")
    for command, what in entries:
        grid.add_row(f"  {command}", what)
    console.print(grid)


# ---------------------------------------------------------------------- história em fases


class StoryProgress:
    """Mostra a execução da história em fases (QA, Dev, Qualidade, Revisão, Portão), uma linha cada.

    O detalhe (passo a passo, arquivos, estados de tarefa) só aparece com `--verbose`; o histórico completo fica em
    `logs <id>`. Problemas (bug, nova tentativa, escalonamento, aviso) aparecem sempre.
    """

    def __init__(self, console: Console, verbose: bool = False) -> None:
        self.console = console
        self.verbose = verbose
        self.phase = ""
        self.story = ""
        self.started = 0.0
        self.files: set[str] = set()
        self.tasks: list[str] = []
        self.parallel = False
        self.cost: dict[str, Any] | None = None
        self.cost_seen = False
        self.status: Status | None = None

    # eventos -------------------------------------------------------------

    def __call__(self, kind: str, data: dict[str, Any]) -> None:
        if kind == "phase":
            self._close_phase()
            self._open_phase(str(data["phase"]), str(data["story"]))
        elif kind == "wave":
            self.tasks += [t for t in data["tasks"] if t not in self.tasks]
            self.parallel = self.parallel or len(data["tasks"]) > 1
        elif kind == "file":
            self.files.add(str(data["path"]))
            if self.verbose:
                self._print(Text(f"      ✎ {data['path']}", style="nexo.muted"))
        elif kind == "cost":
            self.cost = data
            self.cost_seen = True
            pct = data["cost"] / data["cap"] * 100 if data["cap"] else 0
            if pct >= 80:
                self._print(
                    Text(
                        f"   ● custo {brl(data['cost'])} de {brl(data['cap'])} ({pct:.0f}% do teto)",
                        style="nexo.bad" if pct >= 100 else "nexo.warn",
                    )
                )
        elif kind == "gate":
            self._close_phase()
            self._gate(data)
        elif kind == "bug":
            self._print(Text(f"   ↻ {data['bug']}: {data['failures'][0]}", style="nexo.warn"))
        elif kind == "state":
            to = str(data.get("to", ""))
            if to in ("Escalado", "Bloqueado"):
                item = data.get("item") or data.get("task") or ""
                reason = f" ({data['reason']})" if data.get("reason") else ""
                self._print(Text(f"   ✖ {item} {state_label(to)}{reason}".replace("  ", " "), style="nexo.bad"))
            elif self.verbose:
                item = data.get("item") or data.get("task") or ""
                self._print(Text(f"      {item} {data.get('from')} → {to}", style="nexo.muted"))
        elif kind in ("handoff", "retry"):
            label = "troca de agente" if kind == "handoff" else "nova tentativa"
            self._print(Text(f"   ↻ {label}: {data['reason']}", style="nexo.warn"))
        elif kind == "warning":
            self._print(Text(f"   ⚠ {data['message']}", style="nexo.warn"))
        elif kind == "info":
            self._print(Text(f"   {data['message']}", style="nexo.muted"))
        elif self.verbose and kind in ("route", "llm", "step_end", "message"):
            text = data.get("route") or data.get("role") or data.get("status") or data.get("text") or ""
            self._print(Text(f"      {kind}: {str(text).splitlines()[0][:140] if text else ''}", style="nexo.muted"))

    def close(self, report: Any = None) -> None:
        state = getattr(report, "state", "")
        ok = state not in ("Escalado", "Bloqueado")
        self._close_phase(ok=ok)

    # apresentação ----------------------------------------------------------

    def _print(self, renderable: Any) -> None:
        self.console.print(renderable)

    def _open_phase(self, phase: str, story: str) -> None:
        if story and story != self.story:
            self.story = story
            self.console.print()
        self.phase = phase
        self.started = time.monotonic()
        self.files = set()
        self.tasks = []
        self.parallel = False
        if self.console.is_terminal and not self.verbose:
            name = PHASES.get(phase, phase)
            self.status = self.console.status(
                Text(f"{name} · {PHASE_DOING.get(phase, '')}…", style="nexo.muted"), spinner="dots"
            )
            self.status.start()
        else:
            self._print(Text(f"   … {PHASES.get(phase, phase)} · {PHASE_DOING.get(phase, '')}", style="nexo.muted"))

    def _summary(self) -> str:
        files = f"{len(self.files)} arquivo{'s' if len(self.files) != 1 else ''}" if self.files else ""
        if self.phase == "qa":
            return "testes de aceite escritos e travados" + (f" · {files}" if files else "")
        if self.phase == "tasks":
            tasks = " e ".join(self.tasks) if len(self.tasks) <= 2 else f"{len(self.tasks)} tarefas"
            together = " em paralelo" if self.parallel else ""
            return " · ".join(p for p in (f"{tasks}{together}" if self.tasks else "", files) if p) or "tarefas feitas"
        if self.phase == "quality":
            return "lint, testes, segurança e revisão"
        if self.phase == "characterize":
            return "comportamento atual travado em testes"
        if self.phase == "bugfix":
            return "correção aplicada" + (f" · {files}" if files else "")
        return files

    def _close_phase(self, ok: bool = True) -> None:
        if self.status is not None:
            self.status.stop()
            self.status = None
        if not self.phase:
            return
        self._line(
            "✔" if ok else "✖",
            "nexo.good" if ok else "nexo.bad",
            PHASES.get(self.phase, self.phase),
            self._summary(),
            duration(time.monotonic() - self.started) + self._cost_text(),
        )
        self.phase = ""
        self.cost_seen = False

    def _cost_text(self) -> str:
        """Custo acumulado ao fim da fase (com a projeção), só se a fase mediu custo."""
        if not self.cost or not self.cost_seen:
            return ""
        return f" · {brl(self.cost['cost'])} (projeção {brl(self.cost['projection'])})"

    def _gate(self, data: dict[str, Any]) -> None:
        human = data.get("human_areas") or []
        decision = str(data.get("decision", ""))  # auto | human | fail
        needs = decision != "auto"
        score = f"confiança {data['score']:.2f} (limiar {data['threshold']})".replace(".", ",")
        tail = {"auto": " · aprovado sozinho", "fail": " · reprovado"}.get(decision, " · precisa de você")
        if human:
            tail += f" ({', '.join(human)})"
        cost = f"  {brl(self.cost['cost'])}" if self.cost else ""
        self._line(
            "✔" if not needs else ("✖" if decision == "fail" else "●"),
            "nexo.good" if not needs else ("nexo.bad" if decision == "fail" else "nexo.warn"),
            "Portão",
            score + tail,
            cost.strip(),
        )

    def _line(self, mark: str, style: str, name: str, summary: str, tail: str) -> None:
        """Uma linha por fase: marca e nome sempre inteiros; só o resumo encolhe em terminal estreito."""
        head = f"   {mark} {name:<11} "
        room = max(10, self.console.width - len(head) - len(tail) - 2)
        if len(summary) > room:
            summary = summary[: room - 1] + "…"
        line = Text()
        line.append(f"   {mark} ", style=style)
        line.append(f"{name:<11} ")
        line.append(summary)
        if tail:
            line.append(f"  {tail}", style="nexo.muted")
        self.console.print(line, no_wrap=True, overflow="crop")


def stop_active() -> None:
    """Fim do comando (inclusive por erro ou Ctrl+C): para o indicador de andamento sem imprimir nada."""
    status = AI.pop("status", None)
    if status is not None:
        status.stop()
    AI.clear()
    progress = ACTIVE.pop("p", None)
    if progress is not None and progress.status is not None:
        progress.status.stop()
        progress.status = None


def story_report(console: Console, report: Any) -> None:
    """Resumo final da história: estado, qualidade em uma linha, custo e o que fazer agora."""
    style = STATE_STYLE.get(report.state, "nexo.accent")
    head = Text("\n")
    head.append(f"{report.story} ", style="nexo.title")
    head.append(f" {state_label(report.state)} ", style=f"reverse {style}")
    if report.reason:
        head.append(f"  {report.reason}", style="nexo.muted")
    console.print(head)
    if report.quality:
        marks = Text("   qualidade  ", style="nexo.muted")
        for i, stage in enumerate(report.quality):
            ok = stage["status"] == "passed"
            skipped = stage["status"] not in ("passed", "failed", "error")
            mark = "–" if skipped else ("✔" if ok else "✖")
            marks.append(
                f"{mark} {stage['id']}", style="nexo.muted" if skipped else ("nexo.good" if ok else "nexo.bad")
            )
            if i < len(report.quality) - 1:
                marks.append("  ")
        console.print(marks)
        for stage in report.quality:
            if stage["status"] in ("failed", "error"):
                console.print(Text(f"      {stage['id']}: {stage['summary']}", style="nexo.bad"))
    unfinished = {t: s for t, s in report.tasks.items() if s != "Concluido"}
    if unfinished:
        console.print(
            Text("   tarefas  " + " · ".join(f"{t} {state_label(s)}" for t, s in unfinished.items()), style="nexo.warn")
        )
    if report.bugs:
        console.print(Text(f"   bugs  {', '.join(report.bugs)} · {report.attempts} tentativa(s)", style="nexo.warn"))
    if report.cap_brl is not None and report.cost_brl is not None:
        pct = report.cost_brl / report.cap_brl * 100 if report.cap_brl else 0
        estimate = f" · estimativa {brl(report.estimate_brl)}" if report.estimate_brl is not None else ""
        console.print(Text(f"   custo  {brl(report.cost_brl)} de {brl(report.cap_brl)} ({pct:.0f}% do teto){estimate}"))
    if report.branch:
        console.print(Text(f"   branch  {report.branch}", style="nexo.muted"))
    sid = report.story
    nxt: list[str] = []
    if report.state == "AguardandoHumano" and report.cost_gate:
        nxt = [f"approve {sid} --raise 50", f"approve {sid} --economic", f'reject {sid} --reason "..."']
    elif report.state == "AguardandoHumano":
        nxt = [f"approve {sid}", f'reject {sid} --reason "..."']
    elif report.state in ("Bloqueado", "Escalado"):
        nxt = [f"resume {sid}", f"logs {sid}"]
    if nxt and not IN_CONSOLE["on"]:
        console.print("   próximo  " + "  ·  ".join(f"[nexo.accent]{escape(cmd(n))}[/]" for n in nxt))
    console.print()


def prefix() -> str:
    """`nexo ` fora do console; nada dentro dele (lá não se digita `nexo`)."""
    return "" if IN_CONSOLE["on"] else "nexo "


def hint(text: str) -> str:
    """Dica pronta vinda da aplicação (`nexo x y`): dentro do console, sem o `nexo`."""
    return text.removeprefix("nexo ") if IN_CONSOLE["on"] else text


TAGLINE = (
    "Do pedido ao PR com agentes de IA: planeja com você, executa com testes e revisão",
    "e só pede sua decisão no que importa.",
)
FLOW = "preparar a pasta → planejar uma feature → executar as histórias → decidir no portão"


def intro(console: Console, info: Any, brand_name: str = "Nexo", first_time: bool = False) -> None:
    """Apresentação ao abrir o console: marca, versão, o que o produto faz e as ferramentas de IA encontradas."""
    from nexo.cli.render import LOGO

    brain = f"cérebro {info.brain_version}" if info.brain_version else "sem cérebro da org"
    if brand_name.lower() == "nexo":
        for i, row in enumerate(LOGO):
            line = Text(row, style="nexo.brand")
            if i == len(LOGO) - 1:
                line.append(f"  v{info.version} · {brain}", style="nexo.muted")
            console.print(line, no_wrap=True, overflow="crop")
    else:
        line = Text(f" {brand_name}", style="nexo.brand")
        line.append(f"  v{info.version} · {brain}", style="nexo.muted")
        console.print(line)
    console.print()
    for row in TAGLINE:
        console.print(Text(f" {row}"))
    agents = Text(" agentes  ", style="nexo.muted")
    for name, present in info.agents.items():
        agents.append("● " if present else "○ ", style="nexo.good" if present else "nexo.bad")
        agents.append(f"{name}   ", style="" if present else "nexo.muted")
    if not any(info.agents.values()):
        agents.append("nenhuma CLI de IA encontrada: instale e rode `login claude`", style="nexo.warn")
    console.print(agents)
    if first_time:
        console.print(Text(f" como funciona  {FLOW}", style="nexo.muted"))
    console.print()


ROLE_DOING = {
    "analyst": "analisando o requisito funcional",
    "architect": "escrevendo o requisito técnico",
    "briefing": "preparando a explicação da tarefa",
    "breakdown-reviewer": "conferindo se cada tarefa dá para implementar",
    "planner": "planejando a feature",
    "breakdown": "quebrando o plano em histórias e tarefas",
    "discovery": "analisando o discovery",
    "reviewer": "revisando o código",
    "security-reviewer": "revisando a segurança",
    "writer": "escrevendo",
    "explainer": "lendo o código para responder",
    "classifier": "classificando",
    "curator": "organizando as lições",
    "failure-triager": "analisando a falha",
    "handoff": "preparando a troca de agente",
    "assistant": "pensando",
    "integrator": "integrando as partes",
}


class _Elapsed:
    """Texto do indicador que se atualiza sozinho: o que a IA está fazendo e há quanto tempo."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.started = time.monotonic()

    def __rich__(self) -> Text:
        text = Text(f"{self.label}… ", style="nexo.accent")
        text.append(duration(time.monotonic() - self.started), style="nexo.muted")
        text.append("  (Ctrl+C interrompe)", style="nexo.muted")
        return text


AI: dict[str, Any] = {}  # chamada de IA em curso (fora da história, que tem o próprio andamento)


def ai_start(console: Console, role: str, route: str) -> None:
    """A IA começou a trabalhar: indicador com o tempo correndo (no terminal) ou uma linha (em script)."""
    if "p" in ACTIVE:  # dentro da história o andamento é por fase
        return
    ai_stop(console, None)
    doing = ROLE_DOING.get(role, f"trabalhando ({role})")
    label = f"{doing} com {route}"
    AI["started"] = time.monotonic()
    AI["doing"] = doing
    if console.is_terminal:
        status = console.status(_Elapsed(label), spinner="dots")
        status.start()
        AI["status"] = status
    else:
        console.print(Text(f"  … {label}", style="nexo.muted"))


def ai_stop(console: Console, ok: bool | None) -> None:
    """A IA respondeu (ok), falhou e vai tentar outra rota (False) ou o comando acabou (None)."""
    status = AI.pop("status", None)
    if status is not None:
        status.stop()
    started = AI.pop("started", None)
    doing = AI.pop("doing", "")
    if started is None or ok is None:
        return
    took = duration(time.monotonic() - started)
    if ok:
        console.print(Text(f"  ✔ {doing} · {took}", style="nexo.muted"))
    else:
        console.print(Text(f"  ↻ {doing}: a ferramenta falhou em {took}, tentando outra rota", style="nexo.warn"))

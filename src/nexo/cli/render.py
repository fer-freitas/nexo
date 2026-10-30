"""Renderização da apresentação e da ajuda."""

from __future__ import annotations

from rich.console import Console
from rich.markup import escape
from rich.text import Text

from nexo.cli.registry import COMMANDS, GLOBAL_OPTIONS, GROUPS, CommandSpec
from nexo.kernel.finops import money
from nexo.kernel.i18n import get_lang, t
from nexo.modules.setup.app.banner import BannerInfo

LOGO = (
    " ███╗   ███╗███████╗██████╗ ██╗  ██╗ █████╗ ",
    " ████╗ ████║██╔════╝██╔══██╗██║  ██║██╔══██╗",
    " ██╔████╔██║█████╗  ██║  ██║███████║███████║",
    " ██║╚██╔╝██║██╔══╝  ██║  ██║██╔══██║██╔══██║",
    " ██║ ╚═╝ ██║███████╗██████╔╝██║  ██║██║  ██║",
    " ╚═╝     ╚═╝╚══════╝╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝",
)


def banner(console: Console, info: BannerInfo) -> None:
    for i, line in enumerate(LOGO):
        text = Text(line, style="nexo.brand")
        if i == len(LOGO) - 1:
            brain = f"brain {info.brain_version}" if info.brain_version else t("banner.brain_missing")
            text.append(f"  v{info.version} · {brain}", style="nexo.muted")
        console.print(text, highlight=False)
    console.print()
    label_w = 10
    if info.project:
        raw_stacks = info.project.get("stacks") or []
        stacks = ", ".join(str(s) for s in raw_stacks) if isinstance(raw_stacks, list) else "—"
        stacks = stacks or "—"
        squad = info.project.get("squad") or "—"
        project = f"{info.project['name']} ({stacks} · squad {squad})"
        tracker = str(info.project.get("tracker", "local"))
    else:
        project = t("banner.no_project")
        tracker = "local"
    console.print(f" [nexo.title]{t('banner.project'):<{label_w}}[/] {project}", highlight=False)
    console.print(f" [nexo.title]{t('banner.tracker'):<{label_w}}[/] {tracker}", highlight=False)
    agents = Text(f" {t('banner.agents'):<{label_w}} ", style="bold")
    for name, present in info.agents.items():
        agents.append("● " if present else "○ ", style="nexo.good" if present else "nexo.bad")
        agents.append(f"{name}   ", style="" if present else "nexo.muted")
    console.print(agents)
    console.print(f" [nexo.title]{t('banner.budget'):<{label_w}}[/] {_budget_line(info.budget)}", highlight=False)
    console.print(f" [nexo.title]{t('banner.running'):<{label_w}}[/] {_running_line(info.running)}", highlight=False)
    console.print()
    console.print(
        f" {t('banner.start')}  [nexo.accent]nexo init[/]  ·  [nexo.accent]nexo doctor[/]  ·  "
        "[nexo.accent]nexo help[/]",
        highlight=False,
    )


def brl(value: float) -> str:
    return money.brl(value)


def _budget_line(budget: dict[str, object]) -> str:
    if not budget:
        return f"[nexo.muted]{t('banner.budget_na')}[/]"
    spent = float(budget["spent_brl"])  # type: ignore[arg-type]
    limit = budget.get("limit_brl")
    if limit is None:
        line = f"{brl(spent)} [nexo.muted]{t('banner.no_limit')}[/]"
    else:
        ratio = float(budget["ratio"])  # type: ignore[arg-type]
        color = "nexo.bad" if ratio >= 0.9 else "nexo.warn" if ratio >= 0.7 else "nexo.good"
        line = f"[{color}]{brl(spent)}[/] {t('banner.of')} {brl(float(limit))} ({ratio * 100:.0f}%)"  # type: ignore[arg-type]
    devin = (budget.get("units") or {}).get("devin")  # type: ignore[attr-defined]
    if devin:
        line += f" · Devin {devin['used']:g} {t('banner.of')} {devin['included']:g} ACU"
    return line


def _running_line(running: dict[str, int]) -> str:
    if not running:
        return t("banner.none")
    parts = [f"{n} {t('state.' + state)}" for state, n in running.items()]
    return " · ".join(parts)


def _label(spec: CommandSpec) -> str:
    return f"{spec.path} {spec.args}".strip()


def help_all(console: Console) -> None:
    lang = get_lang()
    phase_word = "phase" if lang == "en" else "fase"
    console.print(escape(t("help.usage")), highlight=False)
    shown = [s for s in COMMANDS if not s.hidden]
    width = 38  # rótulo longo vai sozinho na linha e a descrição desce (cabe em 100 colunas)
    for key, names in GROUPS.items():
        console.print()
        console.print(f"[nexo.title]{names[1] if lang == 'en' else names[0]}[/]", highlight=False)
        for spec in (s for s in shown if s.group == key):
            label = _label(spec)
            line = (
                f"  {label:<{width}}{spec.summary(lang)}"
                if len(label) <= width - 2
                else f"  {label}\n  {'':<{width}}{spec.summary(lang)}"
            )
            if spec.available:
                console.print(escape(line), highlight=False)
            else:
                console.print(
                    f"[nexo.muted]{escape(line)}  {escape(f'[{phase_word} {spec.phase}]')}[/]", highlight=False
                )
    console.print()
    console.print(f"[nexo.title]{escape(t('help.global'))}[/]", highlight=False)
    for opt, pt, en in GLOBAL_OPTIONS:
        console.print(escape(f"  {opt:<{width}}{en if lang == 'en' else pt}"), highlight=False)
    console.print()
    tip = (
        "Type `nexo` alone to open the panel (next steps, plain language, no `nexo` prefix)."
        if lang == "en"
        else "Digite só `nexo` para abrir o console (próximos passos, do seu jeito, sem digitar `nexo`)."
    )
    console.print(f"[nexo.muted]{escape(tip)}[/]", highlight=False)


def help_for(console: Console, specs: list[CommandSpec]) -> None:
    lang = get_lang()
    phase_word = "phase" if lang == "en" else "fase"
    for spec in specs:
        status = "" if spec.available else f"  [{phase_word} {spec.phase}]"
        console.print(f"[nexo.title]{escape(f'nexo {_label(spec)}{status}')}[/]", highlight=False)
        console.print(escape(f"  {spec.summary(lang)}"), highlight=False)
        if spec.alias_of:
            now = "now" if lang == "en" else "agora"
            console.print(f"[nexo.muted]  {escape(f'{now}: nexo {spec.alias_of}')}[/]", highlight=False)

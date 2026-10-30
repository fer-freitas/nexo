"""Entendimento determinístico do que a pessoa digita no console.

Ordem: sair/ajuda → número do menu → comando digitado como na CLI → frase comum em português → nada (vai para a IA).
Ids de feature, história e tarefa são reconhecidos no texto; sem id, vale o item em foco da conversa
("aprova ela" depois de falar da US-1-2). Tudo puro: o console decide com o estado que recebe.
"""

from __future__ import annotations

import re
import shlex
import unicodedata
from dataclasses import dataclass, field

FEATURE = re.compile(r"\bF-\d+\b", re.IGNORECASE)
STORY = re.compile(r"\bUS-\d+-\d+\b", re.IGNORECASE)
TASK = re.compile(r"\bT-\d+(?:-\d+)*(?:-[A-Z]+\d*)?\b", re.IGNORECASE)
FILE = re.compile(r"(?<![\w/\\.-])((?:[\w.-]+[/\\])*[\w.-]+\.(?:md|txt|pdf|docx|pptx|xlsx|csv|yaml|yml|json))\b")

EXIT_WORDS = {"sair", "exit", "quit", "tchau", "fim", ":q", "/sair", "/exit", "/quit"}
HELP_WORDS = {"ajuda", "help", "/ajuda", "/help", "comandos", "/comandos", "menu", "/menu"}
NEXT_WORDS = {
    "?",
    "e agora",
    "e agora?",
    "proximo",
    "proximos passos",
    "o que fazer",
    "o que eu faco",
    "pendencias",
    "minhas pendencias",
    "o que espera por mim",
    "o que esta comigo",
    "inbox",
}


@dataclass(frozen=True)
class Intent:
    kind: str  # exit | help | next | menu | command | ask_slot | none
    argv: tuple[str, ...] = ()
    choice: int = 0
    slot: str = ""  # o que falta (feature | story | item | file_or_text)
    note: str = ""  # frase curta para a pessoa ("vou planejar a feature a partir de pedido.md")
    focus: str = ""  # id citado (vira o item em foco)


@dataclass
class Focus:
    """Itens citados na conversa, para entender "ela", "essa", "roda de novo"."""

    feature: str = ""
    story: str = ""
    task: str = ""
    recent: list[str] = field(default_factory=list)

    def see(self, item: str) -> None:
        item = item.upper()
        if STORY.fullmatch(item):
            self.story = item
        elif FEATURE.fullmatch(item):
            self.feature = item
        elif TASK.fullmatch(item):
            self.task = item
        if item in self.recent:
            self.recent.remove(item)
        self.recent.insert(0, item)
        del self.recent[8:]


def normalize(text: str) -> str:
    """Minúsculas, sem acento e sem espaço repetido (o Windows e o Mac digitam igual)."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", plain.strip().lower())


def _ids(text: str) -> tuple[str, str, str]:
    story = STORY.search(text)
    feature = FEATURE.search(text)
    task = TASK.search(text)
    task_id = task.group(0).upper() if task and not (story and task.start() == story.start()) else ""
    return (
        feature.group(0).upper() if feature else "",
        story.group(0).upper() if story else "",
        task_id,
    )


def _split(text: str) -> list[str]:
    try:
        return shlex.split(text, posix=True)
    except ValueError:  # aspas sem par: trata como palavras soltas
        return text.split()


def _has(low: str, *words: str) -> bool:
    return any(re.search(rf"(?<![a-z]){w}(?![a-z])", low) for w in words)


def parse(text: str, commands: set[str], focus: Focus | None = None, menu_size: int = 0) -> Intent:
    """`commands`: primeiras palavras dos comandos da CLI (status, plan, run...)."""
    focus = focus or Focus()
    raw = text.strip()
    low = normalize(raw)
    if not low:
        return Intent("none")
    if low in EXIT_WORDS:
        return Intent("exit")
    if low in HELP_WORDS:
        return Intent("help")
    if low.rstrip("?! .") in {w.rstrip("?") for w in NEXT_WORDS} or low == "?":
        return Intent("next")
    if low.isdigit():
        n = int(low)
        return Intent("menu", choice=n) if 1 <= n <= menu_size else Intent("none", note=f"não há opção {n}")
    words = _split(raw.lstrip("/"))
    if words and words[0].lower() == "nexo":  # hábito antigo: "nexo status" também vale
        words = words[1:]
    if words and words[0].lower() in commands:
        ids = _ids(raw)
        return Intent("command", tuple(words), focus=ids[1] or ids[0] or ids[2])
    return _phrase(raw, low, focus)


def _target(kind: str, ids: tuple[str, str, str], focus: Focus) -> str:
    feature, story, task = ids
    if kind == "story":
        return story or focus.story
    if kind == "feature":
        return feature or focus.feature
    return story or feature or task or focus.story or focus.feature or focus.task


def _phrase(raw: str, low: str, focus: Focus) -> Intent:
    ids = _ids(raw)
    feature, story, task = ids
    cited = story or feature or task

    def need(kind: str, build: tuple[str, ...], note: str) -> Intent:
        target = _target(kind, ids, focus)
        if not target:
            return Intent("ask_slot", build, slot=kind, note=note)  # o console troca {} pelo id respondido
        return Intent("command", (*build, target), note=note.format(target), focus=target)

    # custo antes de "status": "quanto custou a US-1-1" não é andamento
    login = re.search(
        r"\b(login|logar|loga|autenticar|autentica|entrar)\b.*?\b(claude|devin|copilot|codex|gemini|cursor)\b", low
    )
    if login:  # login da ferramenta de IA pelo comando oficial dela
        return Intent("command", ("login", login.group(2)), note=f"login no {login.group(2)}")
    # cada papel do jeito que fala (PM, tech lead, dev)
    if _has(low, "discovery", "vale a pena construir", "vale a pena fazer"):
        idea = re.sub(
            r"(?i)^.*?\b(discovery|vale a pena construir|vale a pena fazer)\b\s*(sobre|de|do|da|para|pra|:)?\s*",
            "",
            raw,
        ).strip(" .:?")
        if len(idea.split()) >= 3:
            return Intent(
                "command", ("plan", "discovery", idea), note="discovery: problema, evidência e se vale construir"
            )
        return Intent("ask_slot", ("plan", "discovery"), slot="file_or_text", note="discovery do PM")
    if _has(low, "devolve", "devolver", "volta para o pm", "manda para o pm", "mandar para o pm"):
        return need("feature", ("tech", "return"), "devolver ao PM as lacunas funcionais da {}")
    if _has(low, "requisito tecnico", "premissas", "design tecnico", "desenho tecnico"):
        return need("feature", ("tech", "spec"), "escrever o requisito técnico da {} (com premissas)")
    if _has(low, "analisa", "analisar", "analise") and (feature or _has(low, "requisito", "funcional")):
        return need("feature", ("tech", "analyze"), "analisar o requisito funcional da {}")
    if _has(low, "minhas tarefas", "minhas tasks", "o que tenho", "tarefas pendentes", "o que falta fazer"):
        return Intent("command", ("tasks",), note="suas tarefas pendentes")
    if _has(low, "explica", "explicar", "entender a", "entender o") and (task or story or focus.task or focus.story):
        target = task or story or focus.task or focus.story
        return Intent("command", ("explain", target), note=f"explicar a {target}", focus=target)
    if _has(low, "valida", "validar", "confere", "conferir", "checa", "checar", "tudo certo"):
        return need("story", ("check",), "conferir se a {} está pronta de verdade")
    if _has(low, "base de conhecimento", "docusaurus", "site da documentacao"):
        return Intent("command", ("docs", "site"), note="atualizar a base de conhecimento do projeto")
    if _has(low, "contexto", "produto"):  # contexto do produto e do projeto
        if _has(low, "configurar", "configura", "definir", "define", "preparar", "prepara", "criar", "cria"):
            return Intent("command", ("context", "init"), note="contar ao Nexo sobre o projeto e o produto")
        return Intent("command", ("context",), note="contexto do produto e do projeto")
    if _has(low, "custo", "custou", "gastei", "gasto", "gastos", "quanto paguei", "fatura"):
        if story or (_has(low, "historia", "ela", "essa") and focus.story):
            target = story or focus.story
            return Intent("command", ("cost", "--story", target), note=f"custo da {target}", focus=target)
        return Intent("command", ("cost",), note="custo do mês")
    if _has(low, "reprova", "reprovar", "rejeita", "rejeitar", "recusa", "recusar"):
        reason = (
            re.sub(r"(?i).*?\b(porque|motivo:?|pois)\b", "", raw).strip()
            if _has(low, "porque", "motivo", "pois")
            else ""
        )
        target = _target("story", ids, focus)
        if not target:
            return Intent("ask_slot", ("reject",), slot="story", note="reprovar o portão")
        if not reason:
            return Intent("ask_slot", ("reject", target, "--reason"), slot="reason", focus=target, note="motivo")
        return Intent("command", ("reject", target, "--reason", reason), note=f"reprovar a {target}", focus=target)
    if _has(low, "aprova", "aprovar", "aprove", "aprovo", "pode seguir"):
        if feature or (_has(low, "plano", "feature", "backlog") and not story):
            return need("feature", ("plan", "approve"), "aprovar o plano da {} e quebrar em histórias")
        return need("story", ("approve",), "aprovar o portão da {}")
    if _has(low, "responde", "responder", "respondo", "duvida", "duvidas", "perguntas"):
        return need("feature", ("plan", "answer"), "responder as dúvidas da {}")
    if _has(low, "retoma", "retomar", "continua", "continuar", "destrava", "destravar"):
        return need("item", ("resume",), "retomar a {}")
    if _has(low, "roda", "rodar", "executa", "executar", "implementa", "implementar", "desenvolve", "desenvolver"):
        if _has(low, "teste", "testes", "qualidade", "pipeline") and not cited:
            return Intent("command", ("quality", "run"), note="pipeline de qualidade do projeto")
        if feature and not story:
            return Intent("command", ("run", "feature", feature), note=f"executar a {feature} inteira", focus=feature)
        if task and not story:
            return Intent("command", ("run", "task", task), note=f"executar a tarefa {task}", focus=task)
        return need("story", ("run", "story"), "executar a {} (QA, dev, qualidade e revisão)")
    if _has(low, "dor", "refinamento", "refinar", "prontidao", "pronta", "pronto") and (story or focus.story):
        target = story or focus.story
        return Intent("command", ("ready", target), note=f"conferir a DoR da {target}", focus=target)
    if _has(low, "log", "logs", "historico", "linha do tempo", "o que aconteceu", "auditoria"):
        target = cited or focus.story or focus.feature
        return Intent("command", ("logs", *([target] if target else [])), note="linha do tempo", focus=target)
    if _has(low, "pr", "pull request", "ci", "merge"):
        return Intent("command", ("pr", "sync"), note="acompanhar PR e CI")
    if _has(low, "diagnostico", "doctor", "ambiente", "instalado", "instalacao", "verificar ferramentas"):
        return Intent("command", ("doctor",), note="diagnóstico do ambiente")
    if _has(low, "inicializar", "inicializa", "init", "preparar o projeto", "configurar o projeto"):
        return Intent("command", ("init",), note="preparar o projeto")
    if _has(low, "status", "andamento", "como esta", "como estao", "situacao", "progresso", "como anda"):
        target = cited
        return Intent("command", ("status", *([target] if target else [])), note="andamento", focus=target)
    if _has(low, "feature", "funcionalidade", "planejar", "planeja", "planejamento", "nova demanda", "demanda"):
        if _has(low, "status", "como") and feature:
            return Intent("command", ("plan", "status", feature), focus=feature)
        file = FILE.search(raw)
        if file:
            return Intent("command", ("plan", file.group(1)), note=f"planejar a partir de {file.group(1)}")
        idea = re.sub(
            r"(?i)^\s*(eu\s+)?(quero|queria|gostaria de|preciso|vamos)?\s*(criar|fazer|planejar|nova|novo|uma|um|"
            r"a|o|feature|funcionalidade|demanda|de|para|pra|\s)*",
            "",
            raw,
        ).strip(" .:")
        if len(idea.split()) >= 3:
            return Intent("command", ("plan", idea), note="planejar a feature a partir da sua descrição")
        return Intent("ask_slot", ("plan",), slot="file_or_text", note="planejar uma feature")
    return Intent("none")


# ------------------------------------------------------------------ confirmação

# Só leem (ou rodam localmente sem custo): executam na hora. O resto pede confirmação.
SAFE: set[tuple[str, ...]] = {
    ("help",),
    ("docs",),
    ("tasks",),
    ("check",),
    ("context",),
    ("version",),
    ("status",),
    ("logs",),
    ("doctor",),
    ("ask",),
    ("cost",),
    ("usage", "show"),
    ("metrics",),
    ("estimate",),
    ("budget", "show"),
    ("plan", "status"),
    ("plan", "roles"),
    ("plan", "inbox"),
    ("plan", "graph"),
    ("approvals", "list"),
    ("config", "get"),
    ("config", "list"),
    ("audit", "verify"),
    ("quality", "run"),
    ("standards", "check"),
    ("standards", "rules"),
    ("skill", "list"),
    ("skill", "show"),
    ("hook", "list"),
    ("hook", "show"),
    ("tool", "list"),
    ("tool", "show"),
    ("mcp", "catalog"),
    ("mcp", "check"),
    ("template", "list"),
    ("template", "show"),
    ("template", "validate"),
    ("template", "preview"),
    ("brain", "status"),
    ("brain", "search"),
    ("brain", "diff"),
    ("learn", "status"),
    ("learn", "patterns"),
    ("killswitch", "status"),
    ("flags", "list"),
    ("insights", "list"),
    ("eval", "report"),
    ("telemetry", "show"),
    ("telemetry", "status"),
    ("graph", "impact"),
    ("graph", "related"),
    ("pricing", "show"),
    ("pricing", "check"),
    ("deps", "scan"),
    ("change", "status"),
    ("modernize", "playbooks"),
    ("modernize", "status"),
    ("program", "status"),
    ("autonomy", "status"),
    ("isolation", "check"),
    ("guard", "status"),
    ("init",),
    # nomes novos dos comandos fundidos
    ("inbox",),
    ("cost", "usage", "show"),
    ("cost", "usage", "scan"),
    ("cost", "budget", "show"),
    ("cost", "pricing", "show"),
    ("cost", "pricing", "check"),
    ("cost", "estimate"),
    ("metrics", "baseline"),
    ("quality", "standards", "check"),
    ("quality", "standards", "rules"),
    ("audit", "evidence", "verify"),
    ("extensions", "skill", "list"),
    ("extensions", "skill", "show"),
    ("extensions", "hook", "list"),
    ("extensions", "hook", "show"),
    ("extensions", "tool", "list"),
    ("extensions", "tool", "show"),
    ("admin", "flags", "list"),
    ("admin", "killswitch", "status"),
    ("admin", "insights", "list"),
    ("admin", "telemetry", "show"),
    ("admin", "telemetry", "status"),
    ("admin", "autonomy", "status"),
    ("admin", "isolation", "check"),
}
# Comandos com efeito no "cost" e no "ready" sem subcomando: só a forma de leitura é segura
UNSAFE_FLAGS = {
    ("cost",): {"close", "reconcile", "chargeback", "set", "watch", "budget", "pricing", "usage"},
    ("ready",): {"--init"},
    ("context",): {"propose", "new", "part", "uses"},
    ("docs",): {"update", "knowledge"},
}
WHY = {
    "plan": "usa IA para planejar",
    "tech": "usa IA (tech lead)",
    "explain": "usa IA para explicar",
    "run": "usa agentes de IA (custo)",
    "resume": "retoma agentes de IA (custo)",
    "approve": "decide o portão",
    "reject": "decide o portão e abre correção",
    "pr": "mexe no PR do GitHub",
    "tracker": "mexe na ferramenta de gestão",
    "docs": "usa IA para documentar",
    "review": "usa IA para revisar",
    "config": "muda a configuração",
}


def needs_confirmation(argv: tuple[str, ...] | list[str]) -> str:
    """'' = roda direto; senão, o motivo curto mostrado no pedido de confirmação."""
    words = [w.lower() for w in argv if not w.startswith("-")]
    flags = {w.lower() for w in argv if w.startswith("-")}
    if not words:
        return ""
    head1, head2 = (words[0],), tuple(words[:2])
    if head2 == ("plan", "classify"):
        return "muda a classificação de dados da feature"
    if head1 == ("ready",):
        return "cria o refinamento" if "--init" in flags else ""
    blocked = UNSAFE_FLAGS.get(head1, set())
    if head1 in SAFE and not (blocked & (set(words[1:]) | flags)):
        return ""
    if head2 in SAFE or tuple(words[:3]) in SAFE:
        return ""
    return WHY.get(words[0], "muda algo no projeto ou fora dele")

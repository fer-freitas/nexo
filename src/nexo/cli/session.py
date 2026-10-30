"""Console interativo: `nexo` sozinho abre uma sessão; dentro dela não se digita mais `nexo`.

Cada linha vira, nesta ordem: sair/ajuda/próximos passos → número do menu → comando da CLI como digitado →
frase comum em português (sem IA) → assistente de IA (só o que sobrou). Comandos que custam ou mudam algo pedem
Enter para confirmar; consultas rodam na hora. Cada comando passa pelo mesmo `main` da CLI (auditoria, feature
flags, erros), então o console faz exatamente o que a linha de comando faria.

Entrada com `prompt_toolkit` (Tab, histórico e edição iguais no Windows, macOS e Linux); sem terminal (testes,
pipe) usa `input()` simples.
"""

from __future__ import annotations

import contextlib
import re
import shlex
import sys
from collections.abc import Callable
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.markup import escape
from rich.text import Text

from nexo.cli import look
from nexo.cli.registry import COMMANDS, top_levels
from nexo.kernel.context import AppContext, Options
from nexo.modules.console.domain.intents import Focus, Intent, needs_confirmation, parse
from nexo.modules.setup.app import pending

ID = re.compile(r"(US-\d+-\d+|F-\d+|T-[\w-]+)")
YES = {"", "s", "sim", "y", "yes", "ok", "confirma", "pode"}

DAILY = (
    ("?", "o que está pendente agora"),
    ("status [id]", "andamento das histórias e tarefas"),
    ("context", "o produto e o que este repositório é"),
    ("logs [id] · cost · doctor", "linha do tempo, custo do mês, ambiente"),
    ("help", "todos os comandos"),
    ("sair", "fecha o console (Ctrl+D também)"),
)
BY_ROLE = (
    (
        "Como PM",
        (
            ('"quero fazer um discovery sobre ..."', "vale a pena construir? problema, evidência, hipóteses"),
            ('plan "ideia" | plan pedido.md', "planejar uma feature nova"),
            ("plan answer F-n", "responder as dúvidas (do refinamento e do tech lead)"),
        ),
    ),
    (
        "Como tech lead",
        (
            ("tech analyze F-n", "analisar o requisito funcional: lacunas, riscos, perguntas ao PM"),
            ("tech spec F-n", "requisito técnico com premissas"),
            ("plan approve F-n", "aprovar o plano e quebrar em histórias"),
            ("ready US-n", "Definition of Ready da história"),
        ),
    ),
    (
        "Como dev",
        (
            ("tasks", "minhas tarefas pendentes"),
            ("explain T-n", "o que a tarefa pede, onde mexer e como validar"),
            ("run story US-n", "executar a história com os agentes"),
            ("approve US-n | reject US-n", "decidir o portão"),
            ("check US-n", "conferir se está tudo certo"),
            ("resume US-n · pr sync", "retomar o que parou; acompanhar o PR e o CI"),
        ),
    ),
)


class Session:
    def __init__(
        self,
        console: Console,
        options: Options,
        read: Callable[[str], str] | None = None,
        run: Callable[[list[str]], int] | None = None,
    ) -> None:
        self.console = console
        self.options = options
        self.focus = Focus()
        self.menu: list[pending.Step] = []
        self.history: list[tuple[str, str]] = []
        self.auto = False  # /auto: não pede confirmação nesta sessão
        self.commands = set(top_levels()) | {"console"}
        self._read = read or self._reader()
        self._run = run or _run_cli

    # ------------------------------------------------------------------ entrada

    def _reader(self) -> Callable[[str], str]:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return input
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.completion import WordCompleter
            from prompt_toolkit.history import FileHistory
        except ImportError:  # pragma: no cover - dependência obrigatória; só por segurança
            return input
        ctx = AppContext(options=self.options)
        ctx.home.mkdir(parents=True, exist_ok=True)
        words = sorted({c.path for c in COMMANDS if not c.hidden} | {"sair", "ajuda", "/auto", "?"})
        session: Any = PromptSession(
            history=FileHistory(str(ctx.home / "console-history.txt")),
            completer=WordCompleter(words, sentence=True, ignore_case=True),
            complete_while_typing=False,
        )
        ctx.close()
        return lambda prompt: str(session.prompt(prompt))

    def ask(self, prompt: str) -> str | None:
        try:
            return self._read(prompt)
        except KeyboardInterrupt:  # Ctrl+C na pergunta: cancela só a pergunta
            return ""
        except EOFError:
            return None

    # ------------------------------------------------------------------ laço

    def loop(self) -> int:
        self.welcome()
        while True:
            line = self.ask("nexo › ")
            if line is None:
                self.console.print()
                break
            try:
                if not self.handle(line):
                    break
            except KeyboardInterrupt:
                self.console.print("[nexo.warn]interrompido[/] — o estado ficou salvo; `resume <id>` continua.")
        self.console.print("[nexo.muted]até mais.[/]")
        return 0

    def handle(self, line: str) -> bool:
        """False = sair."""
        text = line.strip()
        if text.lower() in ("/auto", "auto"):
            self.auto = not self.auto
            state = "sem pedir confirmação" if self.auto else "pedindo confirmação"
            self.console.print(f"[nexo.muted]console {state} para o que custa ou muda algo[/]")
            return True
        intent = parse(text, self.commands, self.focus, len(self.menu))
        return self.act(intent, text)

    def act(self, intent: Intent, text: str) -> bool:
        if intent.kind == "exit":
            return False
        if intent.kind == "help":
            self.show_help()
        elif intent.kind == "next":
            self.show_steps(full=True)
        elif intent.kind == "menu":
            step = self.menu[intent.choice - 1]
            if step.item:
                self.focus.see(step.item)
            self.choose(step)
        elif intent.kind == "command":
            if intent.focus:
                self.focus.see(intent.focus)
            self.execute(intent.argv, intent.note)
        elif intent.kind == "ask_slot":
            self.fill_slot(intent)
        elif text:
            if intent.note:
                self.console.print(f"[nexo.warn]{intent.note}[/]")
            elif len(text) < 4:  # "n", "ok": não vale uma chamada de IA
                self.console.print("[nexo.muted]Não entendi. Digite `?` para os próximos passos ou `ajuda`.[/]")
            else:
                self.assistant(text)
        return True

    # ------------------------------------------------------------------ telas

    def start_autopilot(self) -> None:
        """o que o Nexo faz sozinho (sincronizar, fechar o mês, varrer dependências) em segundo plano.
        `NX_AUTOPILOT=0` desliga (testes, CI e scripts que não querem tarefas em segundo plano)."""
        import os
        import threading

        if os.environ.get("NX_AUTOPILOT", "1") == "0":
            return

        def work() -> None:
            ctx = AppContext(options=self.options)
            try:
                from nexo.modules.setup.app import autopilot

                autopilot.run(ctx)
            except Exception as exc:  # nunca atrapalha o console; fica na trilha
                ctx.audit("autopilot.error", {"error": str(exc)[:200]})
            finally:
                ctx.close()

        threading.Thread(target=work, name="nexo-autopilot", daemon=True).start()

    def welcome(self) -> None:
        self.start_autopilot()
        update = self._brain_check()
        ctx = AppContext(options=self.options)
        try:
            from nexo.modules.setup.app import banner

            head = pending.header(ctx)
            name = _brand_name(ctx)
            look.intro(self.console, banner.collect(ctx), name, first_time=head is None)
            self.offer_brain_update(update)
            if head is None:
                line = Text()
                line.append(name.lower(), style="nexo.brand")
                line.append(" │ ", style="nexo.muted")
                line.append(str(ctx.cwd))
                self.console.print(line)
                self.console.print(
                    "[nexo.muted]Esta pasta ainda não tem o Nexo. Digite 1 para preparar, ou `ajuda`.[/]"
                )
            else:
                look.header(self.console, head, name)
        finally:
            ctx.close()
        self.show_steps(full=True)

    def _brain_check(self) -> Callable[[], dict[str, Any] | None]:
        """Confere em paralelo (com tempo limite) se o cérebro da org tem versão nova; a resposta chega na hora
        de mostrar a apresentação, sem atrasar a abertura do console."""
        import threading

        found: dict[str, Any] = {}

        def work() -> None:
            ctx = AppContext(options=self.options)
            try:
                from nexo.modules.knowledge.app import brain

                found.update(brain.check_update(ctx))
            except Exception:  # sem rede ou sem cérebro configurado: o console segue normalmente
                found["unknown"] = True
            finally:
                ctx.close()

        worker = threading.Thread(target=work, name="nexo-brain-check", daemon=True)
        worker.start()

        def result() -> dict[str, Any] | None:
            worker.join(timeout=6)
            return dict(found) if found else None

        return result

    def offer_brain_update(self, check: Callable[[], dict[str, Any] | None]) -> None:
        """Avisa que o cérebro da org tem versão nova e, se a pessoa confirmar, atualiza na hora (`brain pull`)."""
        info = check()
        if not info or not info.get("repo"):
            return
        local = info.get("version") or "nenhum"
        if info.get("unknown"):
            self.console.print(f"[nexo.muted] cérebro {local} · não deu para conferir atualização agora[/]")
            return
        if not info.get("update"):
            return
        label = (
            "o cérebro da org ainda não foi baixado"
            if info.get("missing")
            else f"há uma versão nova do cérebro da org (você está na {local})"
        )
        self.console.print(f" [nexo.warn]●[/] {label}")
        reply = self.ask("   Atualizar agora? Enter atualiza, n deixa para depois › ")
        if reply is not None and reply.strip().lower() in YES:
            self.execute(["brain", "pull"], "atualizando o cérebro da org", confirmed=True, steps=False)
        else:
            self.console.print("[nexo.muted]   depois: digite `brain pull` quando quiser[/]")
        self.console.print()

    def show_steps(self, full: bool = False) -> None:
        ctx = AppContext(options=self.options)
        try:
            self.menu = pending.next_steps(ctx)
        except Exception as exc:  # o console nunca derruba a sessão por causa do resumo
            self.menu = []
            self.console.print(f"[nexo.warn]não consegui ler os próximos passos: {exc}[/]")
        finally:
            ctx.close()
        if self.focus.recent:  # o item que a pessoa acabou de mexer vem primeiro (os números seguem a tela)
            current = self.focus.recent[0]
            self.menu.sort(key=lambda s: 0 if s.item == current or current in s.argv else 1)
        if not self.menu:
            self.console.print()
            self.console.print(
                '[nexo.good]Nada pendente.[/] Diga o que quer fazer (ex.: plan "nova ideia") ou `ajuda`.'
            )
            self.console.print()
            return
        look.steps(self.console, self.menu if full else self.menu[:5], "Para agora")
        if not full and len(self.menu) > 5:
            self.console.print(f"[nexo.muted]  e mais {len(self.menu) - 5}: digite ? para ver todos[/]\n")

    def show_help(self) -> None:
        look.help_table(self.console, DAILY, "No dia a dia (sem digitar nexo; escreva também do seu jeito)")
        for title, entries in BY_ROLE:
            look.help_table(self.console, entries, title)
        self.console.print("[nexo.muted]  `/auto` liga e desliga a confirmação do que custa ou muda algo.[/]\n")

    # ------------------------------------------------------------------ ações

    def choose(self, step: pending.Step) -> None:
        """Opção do menu: o que falta (ideia, id) é perguntado antes, em vez de rodar o comando incompleto."""
        argv = list(step.argv)
        if argv == ["plan"]:
            self.fill_slot(Intent("ask_slot", ("plan",), slot="file_or_text", note="planejar a feature"))
            return
        if step.needs:
            answer = (self.ask(f"  {' '.join(argv)} {step.needs} › ") or "").strip()
            if not answer:
                self.console.print("[nexo.muted]  cancelado[/]")
                return
            try:
                argv += shlex.split(answer)
            except ValueError:
                argv += answer.split()
        self.execute(argv, step.label)

    def group_menu(self, top: str) -> bool:
        """Grupo digitado sozinho (`cost`, `admin`...): só as opções do grupo, numeradas e com descrição."""
        from nexo.cli.registry import COMMANDS

        specs = [c for c in COMMANDS if c.top == top and " " in c.path and not c.hidden and c.available]
        if not specs:
            return False
        self.menu = [
            pending.Step(c.path, tuple(c.path.split()), c.summary_pt, needs=c.args if "<" in c.args else "")
            for c in specs
        ]
        look.steps(self.console, self.menu, f"O que fazer em {top}")
        return True

    def fill_slot(self, intent: Intent) -> None:
        options = self._candidates(intent.slot)
        if intent.slot == "file_or_text":
            question = "Descreva a ideia ou indique o arquivo do pedido: "
        elif intent.slot == "reason":
            question = "Motivo: "
        else:
            label = {"story": "Qual história", "feature": "Qual feature"}.get(intent.slot, "Qual item")
            question = f"{label}? {('(' + ', '.join(options[:6]) + ') ') if options else ''}"
        answer = (self.ask(question) or "").strip()
        if not answer:
            self.console.print("[nexo.muted]  cancelado[/]")
            return
        if intent.slot in ("story", "feature", "item"):
            answer = answer.upper()
            if not ID.fullmatch(answer):
                self.console.print(f"[nexo.warn]{answer} não é um id (ex.: US-1-2, F-1, T-1-3)[/] — cancelado")
                return
            self.focus.see(answer)
        self.execute((*intent.argv, answer), intent.note.replace("{}", answer))

    def _candidates(self, slot: str) -> list[str]:
        ctx = AppContext(options=self.options)
        try:
            return pending.candidates(ctx, slot)
        finally:
            ctx.close()

    def execute(
        self, argv: tuple[str, ...] | list[str], note: str = "", confirmed: bool = False, steps: bool = True
    ) -> None:
        argv = list(argv)
        if not argv:
            return
        if argv[0].lower() == "console":
            self.console.print("[nexo.muted]você já está no console[/]")
            return
        if argv == ["plan"]:  # planejar sem dizer o quê: pergunta a ideia em vez de mostrar a ajuda do grupo
            self.fill_slot(Intent("ask_slot", ("plan",), slot="file_or_text", note="planejar a feature"))
            return
        if len(argv) == 1 and _bare_group(argv[0]) and self.group_menu(argv[0]):
            return
        why = needs_confirmation(argv)
        shown = " ".join(shlex.quote(a) for a in argv)
        if note:
            self.console.print(f"[nexo.muted]  {escape(note)}[/]")
        if why and not self.auto and not confirmed:
            self.console.print(f"  [nexo.accent]→ {escape(shown)}[/]  [nexo.muted]{escape(why)}[/]")
            reply = self.ask("  Enter confirma, n cancela › ")
            if reply is None or reply.strip().lower() not in YES:  # só Enter/sim confirma; o resto cancela
                self.console.print("[nexo.muted]  cancelado[/]")
                return
        else:
            self.console.print(f"[nexo.muted]  → {escape(shown)}[/]")
        recorder = getattr(self.console, "record", None)
        with contextlib.suppress(AttributeError):
            self.console.record = True
        try:
            code = self._run(argv)
        except KeyboardInterrupt:
            self.console.print("[nexo.warn]interrompido[/] — o estado ficou salvo; `resume <id>` continua.")
            code = 130
        except SystemExit as exc:  # comando que encerra o processo não fecha o console
            code = int(exc.code) if isinstance(exc.code, int) else 1
        output = ""
        with contextlib.suppress(Exception):
            output = self.console.export_text(clear=True)
            self.console.record = bool(recorder)
        self.history.append(("pessoa", shown))
        self.history.append(("nexo", f"saída {code}: {' '.join(output.split())[-600:]}"))
        if self.decide(argv):
            return
        if steps and argv[0] not in ("help",):
            self.show_steps()  # depois de qualquer ação, as opções numeradas do que vem agora

    def decide(self, argv: list[str]) -> bool:
        """a história parou no portão logo depois de rodar? Decide ali mesmo, por tecla."""
        sid = next((a.upper() for a in argv if a.upper().startswith("US-")), "")
        if not sid or argv[0] not in ("run", "resume", "approve"):
            return False
        ctx = AppContext(options=self.options)
        try:
            state = pending.story_state(ctx, sid)
        finally:
            ctx.close()
        if state != "AguardandoHumano":
            return False
        self.console.print()
        reply = (self.ask(f"  Aprovar {sid}?  a aprova · r reprova · d detalhes · Enter depois › ") or "").strip()
        key = reply.lower()[:1]
        if key == "a":
            self.execute(["approve", sid], confirmed=True)
        elif key == "r":
            reason = (self.ask("  Motivo da reprovação: ") or "").strip()
            if reason:
                self.execute(["reject", sid, "--reason", reason], confirmed=True)
            else:
                self.console.print("[nexo.muted]  sem motivo não dá para reprovar; ficou para depois[/]")
        elif key == "d":
            self.execute(["status", sid])
            return self.decide(["approve", sid])
        else:
            self.show_steps()
        return True

    def assistant(self, text: str) -> None:
        from nexo.kernel.errors import NexoError
        from nexo.modules.setup.app import assistant

        ctx = AppContext(options=self.options)
        try:
            head = pending.header(ctx)
            project = (
                f"{head.project} · stacks {', '.join(head.stacks) or '-'} · histórias {head.stories} · tracker "
                f"{head.tracker}"
                if head
                else ""
            )
            steps = "\n".join(f"{i}. {s.label} → {' '.join(s.argv)}" for i, s in enumerate(self.menu, 1))
            catalog = "\n".join(f"{c.path} · {c.args} · {c.summary_pt}" for c in COMMANDS if not c.hidden)
            focus = ", ".join(self.focus.recent[:4])
            answer = assistant.ask(
                ctx,
                text,
                catalog=catalog,
                project=project,
                steps=steps,
                focus=focus,
                history=self.history,
                known=self.commands,
            )
        except NexoError as exc:
            self.console.print(f"[nexo.warn]Não entendi e a IA não está disponível agora ({exc.message}).[/]")
            self.console.print(
                "[nexo.muted]Digite `ajuda` para os comandos do dia a dia ou `?` para os próximos passos.[/]"
            )
            return
        finally:
            ctx.close()
        self.history.append(("pessoa", text))
        self.history.append(("nexo", answer.reply))
        self.console.print(Markdown(answer.reply))
        if answer.argv:
            ids = [a for a in answer.argv if a[:2].upper() in ("US", "F-", "T-")]
            if ids:
                self.focus.see(ids[0])
            self.execute(answer.argv)


def _run_cli(argv: list[str]) -> int:
    from nexo.cli.main import main

    return int(main(argv))


def _bare_group(word: str) -> bool:
    """Grupo que, sem subcomando, só mostraria a própria ajuda (no console vira um menu do grupo)."""
    from typer.main import get_command

    from nexo.cli.app import app

    root = get_command(app)
    cmd = getattr(root, "commands", {}).get(word)
    return cmd is not None and hasattr(cmd, "commands") and bool(getattr(cmd, "no_args_is_help", False))


def _brand_name(ctx: AppContext) -> str:
    from nexo.kernel.errors import NexoError
    from nexo.modules.setup.app import brand

    try:
        return brand.load(ctx.brain_dir).name
    except NexoError:
        return "Nexo"


def start(console: Console, options: Options) -> int:
    look.IN_CONSOLE["on"] = True
    try:
        return Session(console, options).loop()
    finally:
        look.IN_CONSOLE["on"] = False

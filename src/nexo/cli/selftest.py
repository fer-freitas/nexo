"""`nexo selftest`: o fluxo do dia a dia de ponta a ponta, com as ferramentas reais, num projeto descartável.

Tudo passa pelo console (como a pessoa usaria): preparar o projeto → planejar → responder dúvidas → aprovar o plano →
Definition of Ready → executar a história com o agente escolhido (Claude ou Devin) → portão → PR no GitHub → CI.
As demandas ficam nos arquivos do projeto (tracker local). O Nexo do autoteste usa uma pasta própria
(`NX_HOME` isolado): não mexe no seu histórico nem no seu custo. Do cofre do sistema só lê o token do GitHub.

Feito para conferir uma máquina nova (ex.: Windows) com um comando só; o relatório diz o que passou em cada passo.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from nexo.cli.session import Session
from nexo.kernel.context import Options
from nexo.modules.setup.app import selftest_support as support

# Regra simples e sem aritmética: o autoteste confere o fluxo, não a matemática do agente.
PEDIDO = (
    "Mascarar e-mail para exibir na tela: função mascarar_email(email) em src/svc/privacidade.py. Mantém a "
    "primeira letra do usuário, troca o resto do usuário por *** e mantém o domínio (ana.souza@empresa.com vira "
    "a***@empresa.com). E-mail sem @, com usuário vazio ou domínio vazio levanta ValueError com o motivo. Testes com "
    "pytest. Sem dependências novas."
)
FOLLOW_UP = "Confirmo a premissa que você descreveu na pergunta; siga com ela."
ANSWER = (
    "Siga o pedido e a prática de mercado; o que não estiver no pedido fica fora do escopo. Usuário de uma letra "
    "vira a letra seguida de ***. Espaços nas pontas são removidos antes de validar. Erros com ValueError e "
    "mensagem com o motivo."
)
DOR_FILL = {
    "    descricao: ''": "    descricao: Regra coberta por teste unitário (pytest)",
    "      cenario: ''": "      cenario: Função chamada como o consumidor vai usar, sem dublês",
    "    aplica: true\n    cenario: ''": "    aplica: false\n    cenario: ''",
    "    justificativa_nao_aplica: ''": "    justificativa_nao_aplica: Função pura sem I/O; custo desprezível",
    "  logs: []": "  logs:\n    - entrada recusada com o motivo (sem o e-mail)",
    "  metricas: []": "  metricas:\n    - e-mails mascarados e recusados por motivo",
    "  alarmes: []": "  alarmes:\n    - taxa de recusa acima de 30% em 1 hora",
    "  rollback: ''": "  rollback: Reverter para a versão anterior do pacote; sem migração de dados",
    "  pontos: 0": "  pontos: 3",
}
STORY_BRANCH = "nexo/story/US-1-1"


@dataclass
class Result:
    step: str
    ok: bool
    detail: str = ""
    seconds: float = 0.0


@dataclass
class SelfTest:
    console: Console
    tool: str
    github: str = ""
    folder: Path | None = None
    keep: bool = False
    results: list[Result] = field(default_factory=list)

    # ------------------------------------------------------------------ infraestrutura

    @contextlib.contextmanager
    def step(self, name: str) -> Iterator[Result]:
        res = Result(name, False)
        start = time.monotonic()
        self.console.rule(f"[nexo.title]{name}[/]")
        try:
            yield res
        except StepFailed as exc:
            res.ok, res.detail = False, str(exc)
            cause = support.last_agent_error()
            if cause and cause not in res.detail:
                res.detail += f" — último erro de agente: {cause}"
        except Exception as exc:  # o relatório mostra o erro do passo e segue para o resumo
            res.ok, res.detail = False, f"{type(exc).__name__}: {exc}"
        res.seconds = round(time.monotonic() - start, 1)
        self.results.append(res)
        mark = "[nexo.good]✔[/]" if res.ok else "[nexo.bad]✖[/]"
        self.console.print(f"{mark} {name}: {res.detail} ({res.seconds}s)")

    def cli_json(self, *argv: str) -> tuple[int, Any]:
        from nexo.cli.main import main

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(["--json", *argv])
        text = buffer.getvalue().strip()
        try:
            return code, json.loads(text) if text else {}
        except ValueError:
            return code, {"raw": text[-500:]}

    def git(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(
            ["git", "-c", "user.email=selftest@nexo.local", "-c", "user.name=nexo selftest", *args],
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if check and proc.returncode != 0:
            raise StepFailed(f"git {' '.join(args)}: {(proc.stderr or proc.stdout).strip()[-300:]}")
        return proc.stdout.strip()

    # ------------------------------------------------------------------ roteiro

    def run(self) -> int:
        base = self.folder or Path(tempfile.mkdtemp(prefix="nexo-selftest-"))
        self.base = base.resolve()
        self.root = self.base / "projeto"
        home = self.base / "nexo-home"
        previous = os.environ.get("NX_HOME")
        os.environ["NX_HOME"] = str(home)
        self.console.print(f"[nexo.title]Autoteste do Nexo[/] · agente {self.tool} · pasta {self.base}")
        try:
            self._script()
        finally:
            if previous is None:
                os.environ.pop("NX_HOME", None)
            else:
                os.environ["NX_HOME"] = previous
        return self.report()

    def _script(self) -> None:
        checks: list[tuple[str, Callable[[Result], None]]] = [
            ("ferramentas", self.s_tools),
            ("projeto descartável", self.s_project),
            ("console: preparar o projeto", self.s_init),
            ("console: contexto do projeto", self.s_context),
            ("console: planejar a feature", self.s_plan),
            ("console: responder as dúvidas", self.s_answer),
            ("console: análise técnica (tech lead)", self.s_analyze),
            ("console: requisito técnico (tech lead)", self.s_tech_spec),
            ("console: aprovar o plano", self.s_approve_plan),
            ("console: explicar a tarefa (dev)", self.s_explain),
            ("console: Definition of Ready", self.s_ready),
            (f"console: executar a história ({self.tool})", self.s_story),
            ("console: portão", self.s_gate),
            ("console: conferir a história (dev)", self.s_check),
        ]
        if self.github:
            checks.append(("GitHub: PR e CI", self.s_pr))
        for name, fn in checks:
            with self.step(name) as res:
                fn(res)
                res.ok = True
            if not self.results[-1].ok:
                break
        if self.github and not self.keep and self.root.is_dir():
            with self.step("GitHub: limpeza") as res:
                self.s_cleanup(res)

    def session(self) -> Session:
        if not hasattr(self, "_session"):
            self._session = Session(self.console, Options(), read=lambda _prompt: "")
            self._session.auto = True  # o roteiro confirma tudo; a confirmação é testada nos testes do console
        return self._session

    def say(self, line: str) -> None:
        """Uma linha digitada no console."""
        self.console.print(f"[nexo.accent]nexo ›[/] {line}")
        self.session().handle(line)

    # ------------------------------------------------------------------ passos

    def s_tools(self, res: Result) -> None:
        from nexo.kernel.context import AppContext
        from nexo.modules.execution.app import agents as agents_app

        ctx = AppContext(options=Options())
        try:
            states = agents_app.tool_states(ctx, agents_app.adapters(ctx))
        finally:
            ctx.close()
        state = states.get(self.tool)
        if state is None or not state.info.installed:
            raise StepFailed(f"{self.tool} não está instalado (confira com `nexo doctor`)")
        if state.auth.authenticated is False:  # None = a ferramenta não deixa conferir (ex.: Devin): segue
            raise StepFailed(f"{self.tool} instalado mas sem login: rode `nexo login {self.tool}`")
        login = "com login" if state.auth.authenticated else "login não verificável antes de usar"
        missing = [t for t in ("git", "gitleaks") if shutil.which(t) is None]  # gitleaks: etapa obrigatória do portão
        if missing:
            raise StepFailed(f"falta instalar: {', '.join(missing)}")
        if self.github:
            if not support.github_token():
                raise StepFailed("token do GitHub ausente: rode `nexo login github`")
            if "selftest" not in self.github.lower():
                raise StepFailed("use um repositório dedicado com 'selftest' no nome (o autoteste reescreve o main)")
        res.detail = f"{self.tool} {state.info.version or ''} {login} · git · pip"

    def s_project(self, res: Result) -> None:
        if self.root.exists():
            raise StepFailed(f"{self.root} já existe; use outra --dir")
        sample = support.sample_dir()
        for src in sample.rglob("*"):
            if src.is_file() and not {"__pycache__", ".ruff_cache", ".pytest_cache"} & set(src.parts):
                rel = src.relative_to(sample).as_posix()
                rel = (
                    ".gitignore"
                    if rel == "gitignore.txt"
                    else rel.removesuffix(".txt")
                    if rel.endswith(".py.txt")
                    else rel
                )
                dest = self.root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(src.read_bytes())
        home = Path(os.environ["NX_HOME"])
        org = home / "brain" / "org" / "config.yaml"
        org.parent.mkdir(parents=True, exist_ok=True)
        # responsável único (decisão da org): quem pede também aprova o portão
        # sem GitHub, a identidade é a do sistema: o autoteste não pode exigir um login que não vai usar
        identity = "" if self.github else "identity:\n  mode: os\n"
        org.write_text(f"gates:\n  allow_self_approval: true\nguard:\n  auto: false\n{identity}", encoding="utf-8")
        self.git("init", "-q", "-b", "main")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "projeto de exemplo")
        res.detail = str(self.root)

    def s_init(self, res: Result) -> None:
        os.chdir(self.root)
        self.session().welcome()  # como quem digita `nexo` na pasta: resumo e próximos passos
        self.say("1")  # pasta sem Nexo: o primeiro próximo passo é preparar
        if not (self.root / ".nexo" / "nexo.yaml").is_file():
            raise StepFailed("o console não preparou o projeto")
        for key, value in (("data_class", "internal"),):  # código sintético do autoteste
            self.say(f"config set {key} {value} --project")
        if self.github:
            self.say("config set scm.provider github --project")
            self.say(f"config set scm.repo {self.github} --project")
            self.git("remote", "add", "origin", f"https://github.com/{self.github}.git")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "nexo init")
        if self.github:
            self.git("push", "-q", "--force", "origin", "main")  # repositório dedicado (conferido no passo 1)
            self.git("push", "-q", "origin", "--delete", STORY_BRANCH, check=False)
        _, data = self.cli_json("config", "get", "stacks")
        res.detail = f"stack {data.get('value')} · demandas nos arquivos do projeto" + (
            " · GitHub" if self.github else ""
        )

    def s_context(self, res: Result) -> None:
        self.say('context init --standalone --what "serviço de exemplo do autoteste" --users "time de suporte"')
        if not (self.root / ".nexo" / "contexto.md").is_file():
            raise StepFailed("o contexto do projeto não foi criado")
        res.detail = ".nexo/contexto.md criado"

    def _feature_file(self, name: str) -> Path | None:
        return next((f / name for f in self.root.glob(".nexo/specs/F-1-*") if (f / name).is_file()), None)

    def s_analyze(self, res: Result) -> None:
        self.say("analisa o requisito da F-1")
        if self._feature_file("analise-tecnica.md") is None:
            raise StepFailed("a análise técnica não foi gravada")
        res.detail = "analise-tecnica.md gravada"

    def s_tech_spec(self, res: Result) -> None:
        self.say("cria o requisito técnico da F-1")
        if self._feature_file("requisito-tecnico.md") is None:
            raise StepFailed("o requisito técnico não foi gravado")
        res.detail = "requisito-tecnico.md com premissas"

    def s_explain(self, res: Result) -> None:
        self.say("me explica a T-1-1")
        _, data = self.cli_json("tasks")
        if not data:
            raise StepFailed("nenhuma tarefa listada")
        res.detail = f"{sum(len(g['tasks']) for g in data)} tarefa(s); T-1-1 explicada"

    def s_check(self, res: Result) -> None:
        self.say("confere se está tudo certo com a US-1-1")
        _, data = self.cli_json("check", "US-1-1")
        failed = [f"{i['label']}: {i['detail']}" for i in data.get("items", []) if i["ok"] is False]
        if failed:
            raise StepFailed("; ".join(failed))
        res.detail = "tudo certo" if data.get("ok") else "sem pendências (itens ainda não aplicáveis)"

    def s_plan(self, res: Result) -> None:
        self.say(f"quero planejar uma feature: {PEDIDO}")
        _, rows = self.cli_json("plan", "status", "F-1")
        if not rows:
            raise StepFailed("a F-1 não foi criada")
        res.detail = "F-1 criada"

    def s_answer(self, res: Result) -> None:
        """Como uma pessoa: responde e, se a IA fizer perguntas de seguimento (resposta vaga ou premissa a
        confirmar), responde de novo confirmando a premissa proposta — até 3 rodadas."""
        total = rounds = 0
        while rounds < 3:
            pending = support.open_questions(self.root, "F-1")
            if not pending:
                break
            text = ANSWER if rounds == 0 else FOLLOW_UP
            self.say("plan answer F-1 " + " ".join(f'{q.id}="{text}"' for q in pending))
            total += len(pending)
            rounds += 1
        left = support.open_questions(self.root, "F-1")
        if left:
            raise StepFailed(f"{len(left)} dúvida(s) continuam abertas depois de {rounds} rodada(s)")
        res.detail = f"{total} resposta(s) em {rounds} rodada(s)"

    def s_approve_plan(self, res: Result) -> None:
        self.say("aprova o plano da F-1")
        if not support.stories(self.root, "F-1"):  # prontidão < 100%: o responsável aprova com motivo (fluxo real)
            self.say('plan approve F-1 --force --reason "estratégia de testes definida na DoR de cada história"')
        ids = support.stories(self.root, "F-1")
        if not ids:
            raise StepFailed("o plano não foi aprovado")
        res.detail = f"{len(ids)} história(s): " + ", ".join(ids)

    def s_ready(self, res: Result) -> None:
        self.say("ready US-1-1 --init")
        path = support.refinement(self.root, "F-1", "US-1-1")
        if not path.is_file():
            raise StepFailed("refinamento não criado")
        text = path.read_text(encoding="utf-8")
        for old, new in DOR_FILL.items():
            text = text.replace(old, new, 1)
        text = text.replace("estrategia: canario", "estrategia: pacote_versionado")
        path.write_text(text, encoding="utf-8")
        _, data = self.cli_json("ready", "US-1-1")
        ok = bool(data and data[0].get("ready"))
        if not ok:
            raise StepFailed(f"DoR reprovada: {data[0].get('problems') if data else data}")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "F-1 planejada e US-1-1 refinada")
        if self.github:
            self.git("push", "-q", "origin", "main")
        res.detail = "DoR aprovada"

    def s_story(self, res: Result) -> None:
        self.say(f"run story US-1-1 --tool {self.tool}")
        _, data = self.cli_json("status", "US-1-1")
        state = data.get("state")
        if state not in ("AguardandoHumano", "Concluido"):
            runs = sorted(data.get("runs") or [], key=lambda r: str(r.get("started_at") or ""))
            reason = next((str(r["reason"]) for r in reversed(runs) if r.get("reason")), "")
            raise StepFailed(f"história em {state}: {reason}")
        res.detail = f"história em {state}"

    def s_gate(self, res: Result) -> None:
        _, data = self.cli_json("status", "US-1-1")
        if data.get("state") == "AguardandoHumano":
            self.say("aprova a US-1-1")
        _, data = self.cli_json("status", "US-1-1")
        if data.get("state") != "Concluido":
            raise StepFailed(f"história em {data.get('state')}")
        res.detail = "Concluido"

    def _pr(self) -> dict[str, Any] | None:
        return support.find_pr(self.github, STORY_BRANCH)

    def s_pr(self, res: Result) -> None:
        pr = self._pr()
        if pr is None:
            raise StepFailed("o PR não foi aberto no GitHub")
        self.say("como está o PR?")
        self.pr_number = int(pr["number"])
        res.detail = f"PR #{pr['number']} aberto ({pr.get('html_url', '')})"

    def s_cleanup(self, res: Result) -> None:
        number = getattr(self, "pr_number", None)
        if number:
            support.close_pr(self.github, number)
        self.git("push", "-q", "origin", "--delete", STORY_BRANCH, check=False)
        res.ok, res.detail = True, f"PR #{number} fechado e branch apagada" if number else "nada a limpar"

    # ------------------------------------------------------------------ relatório

    def report(self) -> int:
        table = Table(title="Autoteste do Nexo")
        for col in ("passo", "resultado", "detalhe", "tempo"):
            table.add_column(col)
        for r in self.results:
            table.add_row(r.step, "[nexo.good]ok[/]" if r.ok else "[nexo.bad]falhou[/]", r.detail, f"{r.seconds}s")
        self.console.print(table)
        ok = bool(self.results) and all(r.ok for r in self.results) and len(self.results) >= 9
        out = self.base / "selftest.json"
        out.write_text(json.dumps([r.__dict__ for r in self.results], ensure_ascii=False, indent=2), encoding="utf-8")
        verdict = "[nexo.good]TUDO OK[/]" if ok else "[nexo.bad]FALHOU[/] — veja o passo marcado"
        self.console.print(f"{verdict} · relatório em {out}")
        return 0 if ok else 1


class StepFailed(Exception):
    pass

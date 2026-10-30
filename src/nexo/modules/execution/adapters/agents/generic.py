"""Adaptador genérico para CLIs com saída em texto, dirigido por `agents/<tool>/capabilities.yaml`.

Usado por Copilot CLI e Devin CLI enquanto seus contratos não forem verificados.
Sem stream estruturado: chamadas de ferramenta não são observáveis; o controle é feito por flags de negação
e pela verificação do diff ao final do passo.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from nexo.kernel import services
from nexo.kernel.models.agent import (
    ERR_TECHNICAL,
    MESSAGE,
    AgentEvent,
    AgentOutcome,
    AgentRequest,
    AuthStatus,
    Capabilities,
    ToolInfo,
    UsageRecord,
)
from nexo.kernel.runtime.resources import load_yaml
from nexo.kernel.runtime.system import probe, windows_command
from nexo.modules.execution.adapters.agents.base import ERROR_KIND, Patterns, SubprocessSession

copilot_usage = services.load("usage.copilot")
devin_usage = services.load("usage.devin")

# Unidades do consumo real que entram no custo e na telemetria (créditos do Devin ficam só no diagnóstico).
REAL_UNITS = ("acu", "premium_request", "ai_credits", "tokens_in", "tokens_out", "cache_read", "cache_write")


# Ferramenta que, no modo não interativo, só grava arquivos (ex.: Devin sem `dangerous`).
NO_COMMANDS = (
    "\n\n## Como trabalhar nesta ferramenta\n"
    "Aqui você **não executa comandos** (testes, scripts, cálculos, git): só lê e grava arquivos do projeto. "
    "Não tente rodar nada nem pedir aprovação para isso; escreva os arquivos e termine. O Nexo roda os testes e o "
    "pipeline depois e devolve as falhas para você corrigir. Valor esperado de teste: calcule com cuidado e deixe a "
    "conta no próprio teste (fórmula ou comentário), para quem revisar conferir.\n"
)


class TextParser:
    def __init__(self, patterns: Patterns, usage: dict[str, Any], model: str, workdir: str = "") -> None:
        self.patterns = patterns
        self.usage = usage
        self.model = model
        self.workdir = workdir
        self.started = time.time()
        self.lines: list[str] = []

    def _usage(self) -> list[UsageRecord]:
        """Consumo real quando a ferramenta o grava localmente (Devin: sessions.db; Copilot: session-store.db);
        sem a sessão no banco, vale a estimativa marcada como estimada."""
        source = self.usage.get("source")
        if source in ("devin_sessions_db", "copilot_session_store") and self.workdir:
            reader = devin_usage if source == "devin_sessions_db" else copilot_usage
            real = reader.find_usage(self.workdir, self.started, time.time())
            unit = str(self.usage.get("unit", "requests"))
            if real is not None and real.units.get(unit) is not None:
                # a unidade de cobrança entra sempre (mesmo zero): é o peso do rateio da assinatura
                return [UsageRecord(unit, real.units[unit])] + [
                    UsageRecord(u, real.units[u]) for u in REAL_UNITS if u != unit and real.units.get(u)
                ]
        multiplier = float((self.usage.get("model_multiplier") or {}).get(self.model, 1))
        quantity = float(self.usage.get("per_run", 1)) * multiplier
        return [UsageRecord(str(self.usage.get("unit", "requests")), quantity, estimated=True)]

    def feed(self, line: str) -> list[AgentEvent]:
        self.lines.append(line)
        return [AgentEvent(MESSAGE, {"text": line})] if line.strip() else []

    def finish(self, returncode: int, stderr: str) -> AgentOutcome:
        text = "\n".join(self.lines)
        tail = (text[-4000:] + "\n" + stderr[-4000:]).strip()
        usage = self._usage()
        kind = self.patterns.classify(tail)
        if kind == "rate_limit":
            return AgentOutcome("rate_limited", None, "cota esgotada", text=text, usage=usage)
        if returncode != 0 or kind is not None:
            return AgentOutcome(
                "error",
                ERROR_KIND.get(kind or "", ERR_TECHNICAL),
                tail[-500:] or f"saída {returncode}",
                text,
                usage=usage,
            )
        return AgentOutcome("success", text=text, usage=usage)


class GenericTextAdapter:
    def __init__(self, name: str, brain_dir: Path | None = None) -> None:
        self.name = name
        self.spec = load_yaml(f"agents/{name}/capabilities.yaml", brain_dir)
        self.vendor = str(self.spec.get("vendor", name))
        self.executable = str(self.spec.get("executable", name))
        self.patterns = Patterns(load_yaml(f"agents/{name}/patterns.yaml", brain_dir))

    def detect(self) -> ToolInfo:
        p = probe(self.executable)
        return ToolInfo(self.name, p.installed, p.version, p.path)

    def auth_status(self) -> AuthStatus:
        command = self.spec.get("auth_command")
        if not command:
            return AuthStatus(None, "verificação de autenticação não suportada (contrato não verificado)")
        try:
            proc = subprocess.run(
                windows_command(command),
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
                encoding="utf-8",
                errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired):
            return AuthStatus(None, "não foi possível verificar")
        first = (proc.stdout.strip().splitlines() or [""])[0]
        return AuthStatus(proc.returncode == 0, first[:200])

    def capabilities(self) -> Capabilities:
        return Capabilities(
            verified=bool(self.spec.get("verified", False)),
            native_hooks=bool(self.spec.get("native_hooks", False)),
            guard_proven=bool(self.spec.get("guard_proven", False)),
            hooks_file=str(self.spec.get("hooks_file", "")),
            mcp=bool(self.spec.get("mcp", False)),
            remote_execution=bool(self.spec.get("remote_execution", False)),
            usage_unit=str((self.spec.get("usage") or {}).get("unit", "requests")),
        )

    def command(self, req: AgentRequest, exe: str, profile: str = "code-writer") -> list[str]:
        prompt = req.prompt
        if not self.spec.get("runs_commands", True):
            prompt += NO_COMMANDS
        subs = {"{exe}": exe, "{prompt}": prompt, "{model}": req.model, "{workdir}": req.workdir}
        cmd = [subs.get(tok, tok) for tok in self.spec["command"]]
        if self.spec.get("allow_all_flag"):  # modo não interativo exige liberar tudo; a negação vale sobre a liberação
            cmd.append(str(self.spec["allow_all_flag"]))
        profile_args = self.spec.get("profile_args") or {}  # ex.: modo de permissão do Devin por perfil
        cmd += [str(a) for a in profile_args.get(profile, profile_args.get("default", []))]
        deny_flag = self.spec.get("deny_flag")
        if deny_flag:
            by_profile = list((self.spec.get("deny_by_profile") or {}).get(profile, []))
            for item in [*self.spec.get("deny_always", []), *by_profile, *req.denied_tools]:
                cmd += [str(deny_flag), str(item)]
        allow_flag = self.spec.get("allow_flag")
        if allow_flag:
            for item in req.allowed_tools:
                cmd += [str(allow_flag), str(item)]
        mcp_flag = self.spec.get("mcp_flag")
        if mcp_flag and req.mcp_config:
            cmd += [str(mcp_flag), json.dumps(req.mcp_config)]
        return cmd

    def start(self, req: AgentRequest, profile: str = "code-writer") -> SubprocessSession:
        info = self.detect()
        cmd = self.command(req, info.path or self.executable, profile)
        parser = TextParser(self.patterns, dict(self.spec.get("usage") or {}), req.model, req.workdir)
        return SubprocessSession(cmd, req, parser)

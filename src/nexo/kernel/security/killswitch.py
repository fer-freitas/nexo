"""Kill switch: bloquear ferramenta, modelo, versão do Nexo ou do cérebro durante um incidente.

Fontes (a mais restritiva vence, nada se desbloqueia por outra fonte): cérebro (`org/kill-switch.yaml`, assinado),
coletor central (`/v2/kill-switch`, efeito em minutos) e bloqueio local temporário.
Checado no roteamento (rota bloqueada é rejeitada, mesmo se forçada) e no início de cada passo (versão bloqueada
para tudo).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_OPS = ("<=", ">=", "==", "!=", "<", ">")


@dataclass
class KillSwitch:
    tools: dict[str, str] = field(default_factory=dict)  # ferramenta → motivo
    models: dict[str, str] = field(default_factory=dict)  # "ferramenta/modelo" ou "modelo" → motivo
    nexo_versions: dict[str, str] = field(default_factory=dict)  # especificação → motivo
    brain_versions: dict[str, str] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.tools or self.models or self.nexo_versions or self.brain_versions)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tools": self.tools,
            "models": self.models,
            "nexo_versions": self.nexo_versions,
            "brain_versions": self.brain_versions,
            "sources": self.sources,
        }


def _entries(value: Any, default_reason: str) -> dict[str, str]:
    """Aceita lista (`[claude]`) ou mapa com motivo (`{claude: "incidente INC-1"}`)."""
    if isinstance(value, dict):
        return {str(k): str(v or default_reason) for k, v in value.items()}
    if isinstance(value, list):
        return {str(v): default_reason for v in value}
    return {}


def parse(data: dict[str, Any] | None, source: str) -> KillSwitch:
    data = dict(data or {})
    reason = str(data.get("reason") or f"kill switch ({source})")
    ks = KillSwitch(
        tools=_entries(data.get("tools"), reason),
        models=_entries(data.get("models"), reason),
        nexo_versions=_entries(data.get("nexo_versions"), reason),
        brain_versions=_entries(data.get("brain_versions"), reason),
    )
    for spec in [*ks.nexo_versions, *ks.brain_versions]:
        _check_spec(spec)
    if not ks.empty():
        ks.sources.append(source)
    return ks


def merge(*switches: KillSwitch) -> KillSwitch:
    out = KillSwitch()
    for ks in switches:
        for name in ("tools", "models", "nexo_versions", "brain_versions"):
            target: dict[str, str] = getattr(out, name)
            for key, why in getattr(ks, name).items():
                target.setdefault(key, why)
        out.sources += [s for s in ks.sources if s not in out.sources]
    return out


def _version(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", value)
    return tuple(int(p) for p in parts) if parts else ()


def _check_spec(spec: str) -> None:
    for clause in spec.split(","):
        clause = clause.strip()
        if not clause:
            raise ValueError(f"especificação de versão vazia: {spec!r}")
        op = next((o for o in _OPS if clause.startswith(o)), "==")
        rest = clause[len(op) :].strip() if clause.startswith(op) else clause
        if not _version(rest) and rest != "*":
            raise ValueError(f"versão inválida no kill switch: {spec!r}")


def matches(spec: str, version: str) -> bool:
    """`0.13.0`, `<0.14.1`, `>=0.12,<0.13`, `*` (todas). Versões com texto (datas do cérebro) comparam por números."""
    if not version:
        return False
    current = _version(version)
    for clause in (c.strip() for c in spec.split(",")):
        if clause == "*":
            continue
        op = next((o for o in _OPS if clause.startswith(o)), "==")
        target_text = clause[len(op) :].strip() if clause.startswith(op) else clause
        target = _version(target_text)
        ok = {
            "==": current == target,
            "!=": current != target,
            "<": current < target,
            "<=": current <= target,
            ">": current > target,
            ">=": current >= target,
        }[op]
        if not ok:
            return False
    return True


def route_block(ks: KillSwitch, tool: str, model: str) -> str | None:
    if tool in ks.tools:
        return ks.tools[tool]
    for key in (f"{tool}/{model}", model):
        if key in ks.models:
            return ks.models[key]
    return None


def version_block(ks: KillSwitch, nexo_version: str, brain_version: str) -> str | None:
    for spec, why in ks.nexo_versions.items():
        if matches(spec, nexo_version):
            return f"Nexo {nexo_version} bloqueado ({spec}): {why}"
    for spec, why in ks.brain_versions.items():
        if matches(spec, brain_version):
            return f"cérebro {brain_version} bloqueado ({spec}): {why}"
    return None

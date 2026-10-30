"""Dependências e vulnerabilidades em escala: achados das ferramentas de cada stack e o plano de atualização.

Regras (determinísticas):
- vulnerabilidade com versão de correção vira uma história por pacote, sempre;
- versão desatualizada vira história só se a política incluir o tipo de mudança (padrão: correção e menor, juntas
  numa história); mudança de versão maior vira história própria com portão humano, e só com `--major`;
- a atualização é feita pelo Nexo com o gerenciador da stack (nunca pelo agente); a IA só entra se os testes
  quebrarem, pelo ciclo de correção da história.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

MANAGERS = ("pip", "npm", "dotnet", "pub", "maven")
SEVERITY_ORDER = ("low", "medium", "moderate", "high", "critical")


@dataclass(frozen=True)
class Vuln:
    id: str
    severity: str = "medium"
    url: str = ""


@dataclass
class DepFinding:
    manager: str
    package: str
    current: str
    target: str
    kind: str  # security | outdated
    direct: bool = True
    vulns: list[Vuln] = field(default_factory=list)

    @property
    def change(self) -> str:
        return semver_change(self.current, self.target)

    @property
    def worst(self) -> str:
        return max((v.severity for v in self.vulns), key=_sev_rank, default="")


def _sev_rank(sev: str) -> int:
    return SEVERITY_ORDER.index(sev) if sev in SEVERITY_ORDER else 1


def _parts(version: str) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", version.split("+")[0])[:3]] + [0, 0, 0]


def semver_change(current: str, target: str) -> str:
    a, b = _parts(current), _parts(target)
    if b[0] != a[0]:
        return "major"
    if b[1] != a[1]:
        return "minor"
    if b[2] != a[2]:
        return "patch"
    return "none"


def newest(versions: list[str]) -> str:
    return max(versions, key=_parts) if versions else ""


@dataclass
class DepStory:
    title: str
    findings: list[DepFinding]
    human_gate: bool = False
    reason: str = ""


def plan(
    findings: list[DepFinding], include: tuple[str, ...] = ("patch", "minor"), major: bool = False
) -> list[DepStory]:
    stories: list[DepStory] = []
    seen: set[tuple[str, str]] = set()
    for f in sorted(
        (x for x in findings if x.kind == "security" and x.target),
        key=lambda x: (-_sev_rank(x.worst), x.manager, x.package),
    ):
        key = (f.manager, f.package)
        if key in seen:
            continue
        seen.add(key)
        ids = ", ".join(v.id for v in f.vulns[:5])
        gate = f.change == "major"
        stories.append(
            DepStory(
                f"Corrigir vulnerabilidade em {f.package} ({f.current} → {f.target})",
                [f],
                human_gate=gate,
                reason=f"{f.worst or 'vulnerabilidade'}: {ids}" + (" · versão maior: revisão humana" if gate else ""),
            )
        )
    minor: list[DepFinding] = []
    for f in findings:
        if f.kind != "outdated" or (f.manager, f.package) in seen or not f.direct:
            continue
        change = f.change
        if change == "major":
            if major:
                seen.add((f.manager, f.package))
                stories.append(
                    DepStory(
                        f"Atualizar {f.package} para a versão maior {f.target}",
                        [f],
                        human_gate=True,
                        reason=f"{f.current} → {f.target}: pode quebrar compatibilidade; revisão humana",
                    )
                )
        elif change in include:
            seen.add((f.manager, f.package))
            minor.append(f)
    if minor:
        names = ", ".join(f.package for f in minor[:6]) + ("…" if len(minor) > 6 else "")
        stories.append(DepStory(f"Atualizações de versão menor e de correção ({names})", minor, reason="manutenção"))
    return stories


def bump_command(manager: str, package: str, version: str, project: str = "") -> list[str]:
    """Comando do gerenciador da stack que atualiza o pacote (executado pelo Nexo, fora do agente)."""
    if not re.fullmatch(r"[A-Za-z0-9@/._:-]+", package) or not re.fullmatch(r"[A-Za-z0-9.+-]+", version):
        raise ValueError(f"pacote ou versão inválidos: {package} {version}")
    if manager in ("pip", "uv"):  # O Nexo edita requirements/pyproject e instala (`pip_bump`)
        return ["nexo:pip-bump", package, version]
    if manager == "npm":
        return ["npm", "install", f"{package}@{version}", "--ignore-scripts", "--no-audit", "--no-fund"]
    if manager == "dotnet":
        return ["dotnet", "add", *([project] if project else []), "package", package, "--version", version]
    if manager == "pub":  # Vale para Flutter e Dart puro
        return ["dart", "pub", "add", f"{package}:^{version}"]
    if manager == "maven":  # Plugin de versões do Maven; o pacote vem como grupo:artefato
        return [
            "mvn",
            "-B",
            "-q",
            "versions:use-dep-version",
            f"-Dincludes={package}",
            f"-DdepVersion={version}",
            "-DforceVersion=true",
            "-DgenerateBackupPoms=false",
        ]
    raise ValueError(f"gerenciador desconhecido: {manager}")


def as_dict(f: DepFinding) -> dict[str, Any]:
    return {
        "manager": f.manager,
        "package": f.package,
        "current": f.current,
        "target": f.target,
        "kind": f.kind,
        "direct": f.direct,
        "change": f.change,
        "vulns": [{"id": v.id, "severity": v.severity, "url": v.url} for v in f.vulns],
    }


def from_dict(d: dict[str, Any]) -> DepFinding:
    return DepFinding(
        str(d["manager"]),
        str(d["package"]),
        str(d["current"]),
        str(d["target"]),
        str(d["kind"]),
        bool(d.get("direct", True)),
        [Vuln(str(v["id"]), str(v.get("severity", "medium")), str(v.get("url", ""))) for v in d.get("vulns") or []],
    )

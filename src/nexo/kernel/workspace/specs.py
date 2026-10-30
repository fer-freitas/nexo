"""Persistência das especificações em `.nexo/specs/F-<n>-<slug>/`."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from nexo.kernel.backlog import Backlog
from nexo.kernel.errors import NexoError
from nexo.kernel.runtime import yamlfile
from nexo.kernel.spec.model import TEST_TYPES, Spec
from nexo.kernel.spec.questions import Question

FEATURE_DIR = re.compile(r"^F-(\d+)(?:-.*)?$")


class ProjectSettings:
    """O que `.nexo/nexo.yaml` diz sobre onde ficam as demandas e o refinamento do projeto."""

    def __init__(self, root: Path, data: dict[str, Any]) -> None:
        self.root = root
        self.project = str(data.get("project") or root.name)
        team = dict(data.get("team") or {})
        self.team_name = str(team.get("name") or "")
        self.team_repo = str(team.get("repo") or "")

    def team_dir(self) -> Path:
        """Clone do repositório do time; sem time, uma pasta local do projeto."""
        if self.team_name:
            from nexo.kernel.context import nexo_home

            return nexo_home() / "teams" / self.team_name
        return self.root / ".nexo" / "team"

    def project_dir(self) -> Path:
        return self.team_dir() / "projects" / self.project

    @property
    def shared(self) -> bool:
        return bool(self.team_name)


def project_settings(project_root: Path) -> ProjectSettings:
    return ProjectSettings(project_root, yamlfile.load(project_root / ".nexo" / "nexo.yaml"))


def specs_root(project_root: Path) -> Path:
    """Com time configurado, o refinamento mora no repositório do time, para chegar às outras pessoas."""
    settings = project_settings(project_root)
    return settings.project_dir() / "specs" if settings.shared else project_root / ".nexo" / "specs"


def slug(text: str) -> str:
    import unicodedata

    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:40] or "feature"


def next_feature_id(project_root: Path) -> str:
    root = specs_root(project_root)
    numbers = [int(m.group(1)) for p in root.glob("F-*") if p.is_dir() and (m := FEATURE_DIR.match(p.name))]
    return f"F-{max(numbers or [0]) + 1}"


def feature_dir(project_root: Path, feature_id: str) -> Path:
    root = specs_root(project_root)
    for p in sorted(root.glob(f"{feature_id}*")):
        m = FEATURE_DIR.match(p.name)
        if p.is_dir() and m and f"F-{m.group(1)}" == feature_id:
            return p
    raise NexoError(f"feature {feature_id} não encontrada em {root}")


def create_feature_dir(project_root: Path, feature_id: str, title: str) -> Path:
    path = specs_root(project_root) / f"{feature_id}-{slug(title)}"
    (path / "inputs").mkdir(parents=True, exist_ok=True)
    return path


def load_spec(folder: Path) -> Spec:
    return Spec.model_validate(yamlfile.load(folder / "spec.yaml"))


def save_spec(folder: Path, spec: Spec) -> None:
    yamlfile.dump(folder / "spec.yaml", spec.model_dump(mode="json"))
    (folder / "spec.md").write_text(render_markdown(spec), encoding="utf-8")


def load_questions(folder: Path) -> list[Question]:
    data = yamlfile.load(folder / "questions.yaml")
    return [Question.model_validate(q) for q in data.get("questions", [])]


def save_questions(folder: Path, questions: list[Question]) -> None:
    yamlfile.dump(folder / "questions.yaml", {"questions": [q.model_dump() for q in questions]})


def load_backlog(folder: Path) -> Backlog | None:
    path = folder / "backlog.yaml"
    return Backlog.model_validate(yamlfile.load(path)) if path.is_file() else None


def save_backlog(folder: Path, backlog: Backlog) -> None:
    yamlfile.dump(folder / "backlog.yaml", backlog.model_dump(mode="json"))


def save_meta(folder: Path, meta: dict[str, Any]) -> None:
    yamlfile.dump(folder / "meta.yaml", meta)


def load_meta(folder: Path) -> dict[str, Any]:
    return yamlfile.load(folder / "meta.yaml")


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "_(pendente)_"


def render_markdown(spec: Spec) -> str:
    """Visão legível (gerada) — a fonte de verdade é o spec.yaml."""
    parts = [
        f"# {spec.id} — {spec.title}",
        "> Documento gerado pelo Nexo a partir de `spec.yaml`. Edite o YAML ou use `nexo plan feature`.",
        "## Objetivo",
        spec.objective or "_(pendente)_",
        "## Problema",
        spec.problem or "_(pendente)_",
        "## Personas",
        _bullets(spec.personas),
        "## Escopo",
        _bullets(spec.scope_in),
        "## Fora de escopo",
        _bullets(spec.scope_out),
        "## Critérios de aceite",
    ]
    for sc in spec.acceptance:
        steps = (
            [f"  - Dado {g}" for g in sc.given]
            + [f"  - Quando {w}" for w in sc.when]
            + [f"  - Então {t}" for t in sc.then]
        )
        parts.append(f"- **{sc.id}** {sc.title}\n" + "\n".join(steps))
    if not spec.acceptance:
        parts.append("_(pendente)_")
    parts.append("## Contratos")
    if spec.contracts:
        parts.append("\n".join(f"- **{c.id}** ({c.type}) `{c.path}` — {c.summary}" for c in spec.contracts))
    else:
        parts.append(
            f"Não se aplica: {spec.contracts_not_applicable}" if spec.contracts_not_applicable else "_(pendente)_"
        )
    parts.append("## Requisitos não funcionais")
    parts.append("\n".join(f"- **{k}**: {v}" for k, v in spec.nfr.items()) or "_(pendente)_")
    parts.append(f"## Classificação de dados\n{spec.data_class or '_(pendente)_'}")
    parts.append("## Dependências")
    parts.append("Nenhuma." if spec.dependencies_none and not spec.dependencies else _bullets(spec.dependencies))
    parts.append("## Estratégia de testes")
    parts.append("\n".join(f"- **{t}**: {spec.test_strategy.get(t, '_(pendente)_')}" for t in TEST_TYPES))
    parts.append("## Riscos")
    parts.append(
        "\n".join(f"- **{r.area}**: {r.description}" for r in spec.risks)
        or ("Revisados: nenhum risco relevante." if spec.risks_reviewed else "_(pendente)_")
    )
    if spec.assumptions:
        parts.append("## Premissas")
        parts.append(_bullets(spec.assumptions))
    return "\n\n".join(parts) + "\n"

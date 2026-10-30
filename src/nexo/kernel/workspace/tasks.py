"""Fonte local de tarefas: arquivos `.nexo/tasks/<id>.yaml`."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from nexo.kernel.errors import NexoError
from nexo.kernel.models.work import Task
from nexo.kernel.runtime import yamlfile


def tasks_dir(project_root: Path) -> Path:
    return project_root / ".nexo" / "tasks"


def load_task(project_root: Path, task_id: str) -> Task:
    path = tasks_dir(project_root) / f"{task_id}.yaml"
    if not path.is_file():
        raise NexoError(f"tarefa {task_id} não encontrada em {path.parent}")
    try:
        return Task.from_mapping({"id": task_id, **yamlfile.load(path)})
    except ValidationError as exc:
        raise NexoError(f"tarefa {task_id} inválida: {exc.errors()[0]['msg']}") from exc


def list_tasks(project_root: Path) -> list[Task]:
    folder = tasks_dir(project_root)
    if not folder.is_dir():
        return []
    out: list[Task] = []
    for path in sorted(folder.glob("*.yaml")):
        try:
            out.append(Task.from_mapping({"id": path.stem, **yamlfile.load(path)}))
        except (ValidationError, ValueError):
            continue
    return out

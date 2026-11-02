"""Sincronização com a ferramenta de gestão: criação, transições, comentários, fila de retry e reconciliação.

Falhas de integração nunca interrompem o fluxo local: vão para `pending_sync` e são reprocessadas por
`nexo tracker sync`. Mudança feita manualmente no tracker prevalece.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config
from nexo.kernel.runtime.resources import load_yaml
from nexo.kernel.secrets import credentials
from nexo.kernel.tracker.mapping import Mapping
from nexo.modules.demands.adapters.base import Tracker, TrackerItem
from nexo.modules.demands.adapters.file_exchange import FileExchange
from nexo.modules.demands.adapters.github_issues import GitHubIssues
from nexo.store.runs import now_iso, project_key

MAPPING_FILES = {
    "github": "github.yaml",
    "file": "file.yaml",
}


def tracker_config(ctx: AppContext) -> dict[str, Any]:
    return dict(config.resolved(ctx).values.get("tracker") or {"kind": "local"})


def load_mapping(ctx: AppContext, kind: str) -> Mapping:
    cfg = tracker_config(ctx)
    name = str(cfg.get("mapping") or MAPPING_FILES[kind])
    brain = ctx.brain_dir if ctx.brain_dir.is_dir() else None
    project_map = ctx.project_root / ".nexo" / "mappings" / name if ctx.project_root else None
    if project_map and project_map.is_file():
        from nexo.kernel.runtime import yamlfile

        return Mapping.from_mapping(yamlfile.load(project_map))
    return Mapping.from_mapping(load_yaml(f"workflows/mappings/{name}", brain))


def build_tracker(ctx: AppContext) -> Tracker | None:
    cfg = tracker_config(ctx)
    kind = str(cfg.get("kind", "local"))
    if kind == "local":
        return None
    mapping = load_mapping(ctx, kind)
    if kind == "github":
        gh = cfg.get("github") or {}
        return GitHubIssues(str(gh["repo"]), github_token(ctx), mapping)
    if kind == "file":
        root = ctx.project_root or ctx.cwd
        return FileExchange(root / str((cfg.get("file") or {}).get("dir", ".nexo/tracker")), mapping)
    raise NexoError(f"tracker desconhecido: {kind}")


def github_token(ctx: AppContext, required: bool = True) -> str:
    """Token do GitHub para tracker/SCM/cérebro: GitHub App se configurado; senão o token do `nexo login`."""
    from nexo.kernel.secrets import service_identity

    app = dict((config.resolved(ctx).values.get("scm") or {}).get("github_app") or {})
    if app.get("app_id") and app.get("installation_id"):
        key = str(credentials.get_secret("github_app_key") or "")
        if not key:
            raise NexoError("GitHub App sem chave: rode `nexo login github_app_key`")
        return service_identity.github_installation_token(str(app["app_id"]), str(app["installation_id"]), key)
    token = credentials.get_secret("github")
    if not token and required:
        raise NexoError("credencial 'github' ausente: rode `nexo login github` ou configure scm.github_app")
    return token or ""


def _secret(name: str) -> str:
    value = credentials.get_secret(name)
    if not value:
        raise NexoError(f"credencial '{name}' ausente: rode `nexo login {name}`")
    return value


@dataclass
class Sync:
    ctx: AppContext
    root: Path

    def __post_init__(self) -> None:
        self.scope = project_key(self.root)
        self.kind = str(tracker_config(self.ctx).get("kind", "local"))
        self._tracker: Tracker | None = None
        if self.kind != "local":
            from nexo.kernel.platform import flags

            if not flags.enabled(self.ctx, "tracker_sync"):  # segue só local
                self.kind = "local"

    @property
    def enabled(self) -> bool:
        return self.kind != "local"

    def tracker(self) -> Tracker:
        if self._tracker is None:
            built = build_tracker(self.ctx)
            if built is None:
                raise NexoError("tracker local não sincroniza")
            self._tracker = built
        return self._tracker

    # ---------------- vínculos

    def ref(self, local_id: str) -> dict[str, Any] | None:
        row = self.ctx.conn.execute(
            "SELECT * FROM external_refs WHERE project = ? AND local_id = ? AND tracker = ?",
            (self.scope, local_id, self.kind),
        ).fetchone()
        return dict(row) if row else None

    def _save_ref(self, local_id: str, ref: str, url: str, parent: str | None, state: str) -> None:
        self.ctx.conn.execute(
            "INSERT INTO external_refs(project, local_id, tracker, ref, url, parent_local, synced_state, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(project, local_id, tracker) DO UPDATE SET ref = excluded.ref,"
            " url = excluded.url, synced_state = excluded.synced_state, updated_at = excluded.updated_at",
            (self.scope, local_id, self.kind, ref, url, parent, state, now_iso()),
        )

    def _set_synced(self, local_id: str, state: str) -> None:
        self.ctx.conn.execute(
            "UPDATE external_refs SET synced_state = ?, updated_at = ?"
            " WHERE project = ? AND local_id = ? AND tracker = ?",
            (state, now_iso(), self.scope, local_id, self.kind),
        )

    # ---------------- operações (nunca lançam: falha vai para a fila)

    def ensure(
        self,
        local_id: str,
        type_: str,
        title: str,
        state: str,
        description: str = "",
        parent: str | None = None,
        fields: dict[str, Any] | None = None,
    ) -> str | None:
        if not self.enabled:
            return None
        existing = self.ref(local_id)
        if existing:
            if existing["synced_state"] != state:
                self.transition(local_id, state)
            return str(existing["ref"])
        payload = {
            "type": type_,
            "title": title,
            "state": state,
            "description": description,
            "parent": parent,
            "fields": fields or {},
        }
        return self._attempt(local_id, "create", payload)

    def transition(self, local_id: str, state: str, reason: str = "") -> None:
        if not self.enabled:
            return
        if self.ref(local_id) is None:
            row = self.ctx.conn.execute(
                "SELECT type, title FROM work_items WHERE project = ? AND id = ?", (self.scope, local_id)
            ).fetchone()
            if row is None:
                return
            self.ensure(local_id, str(row["type"]), str(row["title"]), state)
            return
        self._attempt(local_id, "transition", {"state": state, "reason": reason})

    def comment(self, local_id: str, text: str) -> None:
        if not self.enabled or self.ref(local_id) is None:
            return
        self._attempt(local_id, "comment", {"text": text})

    def _attempt(self, local_id: str, action: str, payload: dict[str, Any]) -> str | None:
        try:
            result = self._apply(local_id, action, payload)
        except (NexoError, KeyError, OSError, ValueError) as exc:
            message = exc.message if isinstance(exc, NexoError) else f"{type(exc).__name__}: {exc}"
            self.ctx.conn.execute(
                "INSERT INTO pending_sync(project, local_id, tracker, action, payload, error, attempts, created_at)"
                " VALUES (?,?,?,?,?,?,1,?)",
                (self.scope, local_id, self.kind, action, json.dumps(payload, ensure_ascii=False), message, now_iso()),
            )
            self.ctx.audit("tracker.error", {"item": local_id, "action": action, "error": message}, ref=local_id)
            return None
        self.ctx.audit(
            "tracker.sync", {"item": local_id, "action": action, "tracker": self.kind, "result": result}, ref=local_id
        )
        return result

    def _apply(self, local_id: str, action: str, payload: dict[str, Any]) -> str | None:
        tracker = self.tracker()
        if action == "create":
            parent = self.ref(str(payload["parent"])) if payload.get("parent") else None
            item = TrackerItem(
                local_id,
                str(payload["type"]),
                str(payload["title"]),
                str(payload["state"]),
                str(payload.get("description") or ""),
                parent["ref"] if parent else None,
                dict(payload.get("fields") or {}),
            )
            created = tracker.create(item)
            self._save_ref(local_id, created.ref, created.url, payload.get("parent"), item.state)
            return created.ref
        ref = self.ref(local_id)
        if ref is None:
            raise NexoError(f"{local_id} ainda não existe no tracker")
        if action == "transition":
            tracker.transition(str(ref["ref"]), str(payload["state"]), str(payload.get("reason") or ""))
            self._set_synced(local_id, str(payload["state"]))
        elif action == "comment":
            tracker.comment(str(ref["ref"]), str(payload["text"]))
        return str(ref["ref"])

    # ---------------- fila e reconciliação

    def retry_pending(self) -> tuple[int, int]:
        rows = self.ctx.conn.execute(
            "SELECT * FROM pending_sync WHERE project = ? AND tracker = ? ORDER BY id", (self.scope, self.kind)
        ).fetchall()
        ok = failed = 0
        for row in rows:
            try:
                self._apply(str(row["local_id"]), str(row["action"]), json.loads(row["payload"]))
            except (NexoError, KeyError, OSError, ValueError) as exc:
                failed += 1
                message = exc.message if isinstance(exc, NexoError) else str(exc)
                self.ctx.conn.execute(
                    "UPDATE pending_sync SET attempts = attempts + 1, error = ? WHERE id = ?", (message, row["id"])
                )
                continue
            ok += 1
            self.ctx.conn.execute("DELETE FROM pending_sync WHERE id = ?", (row["id"],))
        return ok, failed

    def pending(self) -> int:
        return int(
            self.ctx.conn.execute(
                "SELECT COUNT(*) FROM pending_sync WHERE project = ? AND tracker = ?", (self.scope, self.kind)
            ).fetchone()[0]
        )

    def reconcile(self) -> list[dict[str, str]]:
        """Traz mudanças manuais do tracker: o estado externo prevalece sobre o local."""
        if not self.enabled:
            return []
        rows = self.ctx.conn.execute(
            "SELECT * FROM external_refs WHERE project = ? AND tracker = ?", (self.scope, self.kind)
        ).fetchall()
        by_ref = {str(r["ref"]): dict(r) for r in rows}
        tracker = self.tracker()
        try:
            states = tracker.fetch(list(by_ref))
        except (NexoError, OSError) as exc:
            self.ctx.audit("tracker.error", {"action": "reconcile", "error": str(exc)})
            return []
        changes = []
        for ext in states:
            link = by_ref.get(ext.ref)
            if link is None:
                continue
            nexo_state = tracker.mapping.nexo_state(ext.state, list(ext.tags))
            local = self.ctx.conn.execute(
                "SELECT state FROM work_items WHERE project = ? AND id = ?", (self.scope, link["local_id"])
            ).fetchone()
            if nexo_state is None or local is None or nexo_state in (local["state"], link["synced_state"]):
                continue
            self.ctx.conn.execute(
                "UPDATE work_items SET state = ?, updated_at = ? WHERE project = ? AND id = ?",
                (nexo_state, now_iso(), self.scope, link["local_id"]),
            )
            self._set_synced(str(link["local_id"]), nexo_state)
            change = {
                "item": str(link["local_id"]),
                "from": str(local["state"]),
                "to": nexo_state,
                "external": ext.state,
            }
            self.ctx.audit("tracker.reconciled", change, ref=str(link["local_id"]))
            changes.append(change)
        return changes


def for_root(ctx: AppContext, root: Path) -> Sync:
    return Sync(ctx, root)


# ---------------- comandos `nexo tracker ...`


def check_mapping(ctx: AppContext) -> dict[str, Any]:
    from nexo.kernel.tracker.mapping import check

    kind = str(tracker_config(ctx).get("kind", "local"))
    if kind == "local":
        raise NexoError("tracker local não usa de-para (config tracker.kind)")
    mapping = load_mapping(ctx, kind)
    tracker = build_tracker(ctx)
    process = tracker.describe_process() if tracker else None
    problems = check(mapping, process)
    return {
        "tracker": kind,
        "process": process.name if process else None,
        "problems": problems,
        "mode": "automático (processo lido)" if process else "manual (processo não legível)",
    }


def suggest_mapping(ctx: AppContext) -> Path:
    from nexo.kernel.runtime import yamlfile
    from nexo.kernel.tracker.mapping import suggest

    kind = str(tracker_config(ctx).get("kind", "local"))
    tracker = build_tracker(ctx)
    process = tracker.describe_process() if tracker else None
    if process is None:
        raise NexoError("não foi possível ler o processo da ferramenta; edite o de-para manualmente (modo 2)")
    current = load_mapping(ctx, kind)
    proposal = suggest(process, kind, dict(current.types))
    root = ctx.project_root or ctx.cwd
    target = root / ".nexo" / "mappings" / MAPPING_FILES[kind]
    yamlfile.dump(target, proposal)
    ctx.audit("tracker.mapping_suggested", {"tracker": kind, "process": process.name, "path": str(target)})
    return target


def export_file(ctx: AppContext, dest: Path) -> tuple[int, Path]:
    """Exporta os itens locais no formato de importação (CSV) — modo 3, sem API. `dest` é pasta ou `.csv`."""
    import shutil
    import tempfile

    root = ctx.project_root
    if root is None:
        raise NexoError("nenhum projeto Nexo encontrado")
    if dest.suffix.lower() == ".csv":
        with tempfile.TemporaryDirectory() as tmp:
            n, written = export_file(ctx, Path(tmp))
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(written), dest)
        return n, dest
    mapping = load_mapping(ctx, "file")
    fx = FileExchange(dest, mapping)
    rows = ctx.conn.execute(
        "SELECT id, type, title, state FROM work_items WHERE project = ? ORDER BY id", (project_key(root),)
    ).fetchall()
    for row in rows:
        state = str(row["state"])
        state = {"Aberto": "EmDev", "Resolvido": "Concluido"}.get(state, state)
        fx.create(TrackerItem(str(row["id"]), str(row["type"]), str(row["title"]), state))
    if not rows:
        fx._write([])  # sem itens, o arquivo sai só com o cabeçalho, pronto para a ferramenta
    ctx.audit("tracker.exported", {"items": len(rows), "path": str(fx.outbox)})
    return len(rows), fx.outbox


def import_file(ctx: AppContext, source: Path) -> list[dict[str, str]]:
    """Importa estados de um CSV exportado da ferramenta (a mudança externa prevalece)."""
    import shutil

    root = ctx.project_root
    if root is None:
        raise NexoError("nenhum projeto Nexo encontrado")
    if source.is_dir() and (source / "outbox.csv").is_file():
        source = source / "outbox.csv"  # a pasta que o `tracker export` criou
    if not source.is_file():
        raise NexoError(f"arquivo CSV não encontrado: {source}")
    mapping = load_mapping(ctx, "file")
    folder = root / ".nexo" / "tracker"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, folder / "inbox.csv")
    fx = FileExchange(folder, mapping)
    scope = project_key(root)
    rows = ctx.conn.execute("SELECT id, state FROM work_items WHERE project = ?", (scope,)).fetchall()
    local = {str(r["id"]): str(r["state"]) for r in rows}
    changes = []
    for ext in fx.fetch([f"file:{i}" for i in local]):
        item = ext.ref.removeprefix("file:")
        nexo_state = mapping.nexo_state(ext.state, list(ext.tags))
        if nexo_state and nexo_state != local.get(item):
            ctx.conn.execute(
                "UPDATE work_items SET state = ?, updated_at = ? WHERE project = ? AND id = ?",
                (nexo_state, now_iso(), scope, item),
            )
            change = {"item": item, "from": local.get(item, ""), "to": nexo_state, "external": ext.state}
            ctx.audit("tracker.reconciled", change, ref=item)
            changes.append(change)
    return changes

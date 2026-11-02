"""O que acontece na execução vira demanda: os módulos publicam eventos, e as demandas ouvem."""

from __future__ import annotations

from typing import Any

from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.runtime import yamlfile
from nexo.kernel.workspace import specs
from nexo.modules.demands.app import core, flow
from nexo.modules.demands.domain import model as m

ROLE_PART = {"role:requisitos": "requisitos", "role:design": "design"}
DRAFT_FILE = "plan.draft.yaml"


def handle(ctx: AppContext, type_: str, payload: dict[str, Any], ref: str | None) -> None:
    if ctx.project_root is None:
        return
    handler = HANDLERS.get(type_)
    if handler is not None:
        handler(ctx, payload, ref)


def _feature_created(ctx: AppContext, payload: dict[str, Any], ref: str | None) -> None:
    fid = str(payload.get("feature") or ref or "")
    if fid:
        flow.adopt_feature(ctx, fid, fid)


def _feature_title(ctx: AppContext, fid: str) -> None:
    try:
        spec = specs.load_spec(specs.feature_dir(core.root_of(ctx), fid))
    except NexoError:
        return
    demands = core.load(ctx, sync=False)
    if fid in demands and spec.title and demands[fid].title in ("", fid):
        core.record(ctx, "demand.titled", fid, {"title": core.clean(spec.title)[:200]}, push=False)


def _packaged(ctx: AppContext, payload: dict[str, Any], ref: str | None) -> None:
    fid = str(payload.get("feature") or ref or "")
    flow.adopt_feature(ctx, fid, fid)
    _feature_title(ctx, fid)
    folder = specs.feature_dir(core.root_of(ctx), fid)
    draft = yamlfile.load(folder / DRAFT_FILE)
    flow.adopt_backlog(ctx, fid, list(draft.get("stories") or []), list(draft.get("tasks") or []))
    core.store(ctx).push(f"nexo: rascunho de {fid}")


def _gate(ctx: AppContext, payload: dict[str, Any], ref: str | None) -> None:
    gate = str(payload.get("gate", ""))
    fid = str(payload.get("feature") or ref or "")
    if gate in ROLE_PART and payload.get("decision") == "approved":
        flow.adopt_feature(ctx, fid, fid)
        from nexo.kernel.roles import part_digests  # o hash do pacote; o das mudanças aceitas soma depois

        _ = part_digests
        core.record(
            ctx, "demand.approved", fid, {"part": ROLE_PART[gate], "digest": str(payload.get("digest", ""))}, push=False
        )
        _feature_title(ctx, fid)
        core.store(ctx).push(f"nexo: {gate} de {fid}")
    elif gate == "spec" and payload.get("decision") == "approved":
        flow.adopt_feature(ctx, fid, fid)
        _feature_title(ctx, fid)
        backlog = specs.load_backlog(specs.feature_dir(core.root_of(ctx), fid))
        if backlog is not None:
            flow.adopt_backlog(
                ctx,
                fid,
                [s.model_dump(mode="json") for s in backlog.stories],
                [t.model_dump(mode="json") for t in backlog.tasks],
            )
        core.store(ctx).push(f"nexo: backlog de {fid}")


def _incident(ctx: AppContext, payload: dict[str, Any], ref: str | None) -> None:
    fid = str(payload.get("feature") or ref or "")
    flow.adopt_feature(ctx, fid, str(payload.get("title") or fid), origin="incident")
    # o requisito é o próprio incidente, já aprovado na abertura: a demanda não para na PM
    core.record(ctx, "demand.approved", fid, {"part": "requisitos", "digest": "incident"}, push=False)
    backlog = specs.load_backlog(specs.feature_dir(core.root_of(ctx), fid))
    if backlog is not None:
        flow.adopt_backlog(
            ctx,
            fid,
            [s.model_dump(mode="json") for s in backlog.stories],
            [t.model_dump(mode="json") for t in backlog.tasks],
            origin="incident",
        )
    core.store(ctx).push(f"nexo: incidente em {fid}")


def _state(ctx: AppContext, payload: dict[str, Any], ref: str | None) -> None:
    item = str(payload.get("item") or payload.get("task") or ref or "")
    to = str(payload.get("to", ""))
    demands = core.load(ctx, sync=False)
    if item in demands and to and demands[item].state != to:
        core.record(ctx, "demand.state", item, {"state": to, "from": str(payload.get("from", ""))})


def _bug(ctx: AppContext, payload: dict[str, Any], ref: str | None) -> None:
    bug = str(payload.get("bug", ""))
    story = str(payload.get("story") or ref or "")
    demands = core.load(ctx, sync=False)
    if not bug or bug in demands:
        return
    core.record(
        ctx,
        "demand.created",
        bug,
        {
            "type": "bug",
            "title": f"{story}: falha no portão ({payload.get('source', '')})",
            "parent": story if story in demands else "",
            "spawned_by": story,
            "project": core.store(ctx).project,
            "state": "EmDev",
            "origin": {"kind": "gate", "source": str(payload.get("source", ""))},
        },
    )


def _ready(ctx: AppContext, payload: dict[str, Any], ref: str | None) -> None:
    story = str(payload.get("story") or ref or "")
    demands = core.load(ctx, sync=False)
    if story in demands:
        core.record(ctx, "demand.dor", story, {"passed": bool(payload.get("ready"))}, push=False)


def _pr(ctx: AppContext, payload: dict[str, Any], ref: str | None) -> None:
    story = str(payload.get("story") or ref or "")
    demands = core.load(ctx, sync=False)
    if story in demands:
        core.record(ctx, "demand.linked", story, {"links": {"pr": str(payload.get("pr", ""))}})


def _merged(ctx: AppContext, payload: dict[str, Any], ref: str | None) -> None:
    story = str(payload.get("story") or ref or "")
    demands = core.load(ctx, sync=False)
    if story in demands:
        core.record(ctx, "demand.merged", story, {"pr": str(payload.get("pr", ""))})


def _review_finding(ctx: AppContext, payload: dict[str, Any], ref: str | None) -> None:
    """Achado gerado por IA nasce em triagem, para não virar trabalho sem alguém aceitar."""
    story = str(payload.get("story") or ref or "")
    demands = core.load(ctx, sync=False)
    if story not in demands or not payload.get("issue"):
        return
    fid = m.merge_conflict_free_id(list(demands), f"FIND-{story.removeprefix('US-')}")
    core.record(
        ctx,
        "demand.created",
        fid,
        {
            "type": "finding",
            "title": core.clean(str(payload.get("issue")))[:200],
            "parent": story,
            "spawned_by": story,
            "project": core.store(ctx).project,
            "origin": {"kind": "review"},
            "triage": True,
        },
    )


HANDLERS = {
    "plan.created": _feature_created,
    "plan.packaged": _packaged,
    "gate.decision": _gate,
    "state.transition": _state,
    "bug.created": _bug,
    "ready.checked": _ready,
    "scm.pr": _pr,
    "scm.merged": _merged,
    "review.spec_issue_ignored": _review_finding,
    "incident.planned": _incident,
}

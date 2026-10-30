"""Feature em vários repositórios: planejamento único, execução por repositório e PRs ligados entre si.

Fluxo: `nexo program repo add <nome> <caminho> [--after outro] --workspace W` declara os repositórios da área de
trabalho; o planejamento (`nexo plan … --workspace W`, `plan approve`) gera histórias com o campo `repo`;
`nexo program split F-n --workspace W` cria em cada repositório uma feature normal com as histórias dele;
`nexo program run F-n --workspace W` executa os repositórios na ordem das dependências e liga os PRs;
`nexo program status` mostra tudo junto.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from nexo.kernel.context import PROJECT_DIR, AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import flags
from nexo.kernel.runtime import yamlfile
from nexo.kernel.workspace import specs

STATE_FILE = "program.yaml"


def _workspace(ctx: AppContext, name: str) -> Path:
    from nexo.kernel import services

    workspace_root = services.get("planning.plan", "workspace_root")

    return cast("Path", workspace_root(ctx, name))


def _cfg_path(root: Path) -> Path:
    return root / PROJECT_DIR / "config.yaml"


def repos(ctx: AppContext, workspace: str) -> dict[str, dict[str, Any]]:
    cfg = yamlfile.load(_cfg_path(_workspace(ctx, workspace)))
    return {str(k): dict(v) for k, v in dict((cfg.get("program") or {}).get("repos") or {}).items()}


def add_repo(ctx: AppContext, workspace: str, name: str, path: Path, after: list[str]) -> dict[str, Any]:
    flags.require(ctx, "program")
    target = path.expanduser().resolve()
    if not (target / PROJECT_DIR).is_dir():
        raise NexoError(f"{target} não é um projeto Nexo (rode nexo init lá)")
    root = _workspace(ctx, workspace)
    cfg = yamlfile.load(_cfg_path(root))
    program = dict(cfg.get("program") or {})
    known = dict(program.get("repos") or {})
    unknown = [a for a in after if a not in known]
    if unknown:
        raise NexoError(f"repositório(s) não declarados: {', '.join(unknown)}")
    known[name] = {"path": str(target), "after": list(after)}
    program["repos"] = known
    cfg["program"] = program
    yamlfile.dump(_cfg_path(root), cfg)
    ctx.audit("program.repo_added", {"workspace": workspace, "repo": name, "after": after})
    return known


def repo_order(declared: dict[str, dict[str, Any]], extra: dict[str, set[str]] | None = None) -> list[str]:
    """Ordem dos repositórios: quem fornece (declarado em `after` ou dependência de história) vem antes."""
    deps = {name: set(v.get("after") or []) for name, v in declared.items()}
    for name, more in (extra or {}).items():
        deps.setdefault(name, set()).update(more - {name})
    order: list[str] = []
    while deps:
        ready = sorted(k for k, d in deps.items() if not (d & set(deps)))
        if not ready:
            raise NexoError(f"ciclo entre repositórios: {', '.join(sorted(deps))}")
        order += ready
        for k in ready:
            deps.pop(k)
    return order


def split(ctx: AppContext, workspace: str, fid: str) -> dict[str, Any]:
    """Distribui as histórias aprovadas (campo `repo`) numa feature normal dentro de cada repositório."""
    flags.require(ctx, "program")
    from nexo.kernel import services

    Planner = services.get("planning.plan", "Planner")
    from nexo.kernel.backlog import Backlog

    root = _workspace(ctx, workspace)
    folder = specs.feature_dir(root, fid)
    backlog = specs.load_backlog(folder)
    if backlog is None:
        raise NexoError(f"{fid} ainda não tem backlog aprovado (nexo plan approve {fid} --workspace {workspace})")
    if (folder / STATE_FILE).is_file():
        raise NexoError(f"{fid} já foi distribuída (nexo program status)")
    declared = repos(ctx, workspace)
    missing = sorted({s.repo or "—" for s in backlog.stories if s.repo not in declared})
    if missing:
        raise NexoError(f"histórias sem repositório declarado: {', '.join(missing)} (nexo program repo add)")
    story_repo = {s.id: s.repo for s in backlog.stories}
    cross: dict[str, set[str]] = {}
    for t in backlog.tasks:
        for dep in t.depends_on:
            dep_story = next((x.story for x in backlog.tasks if x.id == dep), None)
            if dep_story and story_repo[dep_story] != story_repo[t.story]:
                cross.setdefault(story_repo[t.story], set()).add(story_repo[dep_story])
    order = repo_order(declared, cross)
    spec = specs.load_spec(folder)
    state: dict[str, Any] = {"feature": fid, "order": order, "repos": {}}
    for name in order:
        stories = [s for s in backlog.stories if s.repo == name]
        if not stories:
            continue
        repo_ctx = AppContext(cwd=Path(declared[name]["path"]), home=ctx.home, options=ctx.options)
        try:
            planner = Planner(repo_ctx)
            local = specs.next_feature_id(planner.root)
            n_old, n_new = fid.split("-")[1], local.split("-")[1]

            def rename(item_id: str, n_old: str = n_old, n_new: str = n_new) -> str:
                prefix, rest = item_id.split("-", 1)
                return f"{prefix}-{n_new}-{rest.split('-', 1)[1]}" if rest.startswith(n_old + "-") else item_id

            ids = {s.id for s in stories}
            tasks = [t for t in backlog.tasks if t.story in ids]
            local_tasks = {t.id for t in tasks}
            new_backlog = Backlog(
                feature=local,
                stories=[s.model_copy(update={"id": rename(s.id)}) for s in stories],
                tasks=[
                    t.model_copy(
                        update={
                            "id": rename(t.id),
                            "story": rename(t.story),
                            "depends_on": [rename(d) for d in t.depends_on if d in local_tasks],
                        }
                    )
                    for t in tasks
                ],
            )
            local_spec = spec.model_copy(update={"id": local, "title": f"{spec.title} ({name})"})
            new_folder = specs.create_feature_dir(planner.root, local, local_spec.title)
            specs.save_spec(new_folder, local_spec)
            specs.save_questions(new_folder, [])
            yamlfile.dump(new_folder / "program.yaml", {"program": fid, "workspace": workspace, "repo": name})
            planner.store.upsert_item(local, "feature", local_spec.title, planner.project, "local", "Ideia")
            planner._transition(local, "plan")
            planner.materialize(new_folder, local_spec, new_backlog)
            state["repos"][name] = {
                "path": declared[name]["path"],
                "feature": local,
                "stories": {s.id: rename(s.id) for s in stories},
            }
        finally:
            repo_ctx.close()
    yamlfile.dump(folder / STATE_FILE, state)
    ctx.audit("program.split", {"feature": fid, "order": order, "repos": list(state["repos"])}, ref=fid)
    return state


def _state(ctx: AppContext, workspace: str, fid: str) -> tuple[Path, dict[str, Any]]:
    folder = specs.feature_dir(_workspace(ctx, workspace), fid)
    state = yamlfile.load(folder / STATE_FILE)
    if not state:
        raise NexoError(f"{fid} ainda não foi distribuída (nexo program split {fid} --workspace {workspace})")
    return folder, state


def run(ctx: AppContext, workspace: str, fid: str, parallel: int = 1) -> dict[str, Any]:
    """Executa as features de cada repositório na ordem; para quando um repositório não conclui."""
    from nexo.kernel import services

    feature = services.load("execution.feature")

    _, state = _state(ctx, workspace, fid)
    results: dict[str, Any] = {}
    stopped = ""
    for name in state["order"]:
        info = state["repos"].get(name)
        if info is None:
            continue
        repo_ctx = AppContext(cwd=Path(info["path"]), home=ctx.home, options=ctx.options)
        try:
            out = feature.run_feature(repo_ctx, info["feature"], parallel)
        finally:
            repo_ctx.close()
        results[name] = out
        done = all(r.get("state") == "Concluido" for r in out.get("stories") or [])
        if not done:
            stopped = f"{name} não concluiu; os repositórios seguintes esperam"
            break
    links = link_prs(ctx, workspace, fid)
    ctx.audit("program.run", {"feature": fid, "repos": list(results), "stopped": stopped, "prs": len(links)}, ref=fid)
    return {"feature": fid, "repos": results, "stopped": stopped, "prs": links}


def _prs(info: dict[str, Any]) -> list[str]:
    from nexo.kernel import services

    RunOptions = services.get("execution.run", "RunOptions")
    from nexo.kernel import services

    StoryRunner = services.get("execution.story", "StoryRunner")

    repo_ctx = AppContext(cwd=Path(info["path"]), home=info.get("home") or AppContext().home)
    try:
        runner = StoryRunner(repo_ctx, RunOptions())
        urls = []
        for sid in info["stories"].values():
            run = runner.story_run(sid)
            if run is not None:
                url = runner._load_state(run.id).get("pr")
                if url:
                    urls.append(str(url))
        return urls
    finally:
        repo_ctx.close()


def link_prs(ctx: AppContext, workspace: str, fid: str) -> list[str]:
    """Liga os PRs do programa: cada PR recebe a lista dos outros (quando o SCM está configurado no repositório)."""
    _, state = _state(ctx, workspace, fid)
    by_repo = {name: _prs({**info, "home": ctx.home}) for name, info in state["repos"].items()}
    every = [u for urls in by_repo.values() for u in urls]
    if len(every) < 2:
        return every
    for name, urls in by_repo.items():
        info = state["repos"][name]
        repo_ctx = AppContext(cwd=Path(info["path"]), home=ctx.home, options=ctx.options)
        try:
            from nexo.kernel import services

            delivery_scm = services.load("delivery.scm")

            if not delivery_scm.enabled(repo_ctx):
                continue
            scm = delivery_scm.build(repo_ctx)
            others = [u for u in every if u not in urls]
            note = f"\n\n---\nPrograma {fid} (Nexo) · PRs relacionados, merge na ordem {' → '.join(state['order'])}:\n"
            note += "\n".join(f"- {u}" for u in others)
            for url in urls:
                number = int(url.rstrip("/").rsplit("/", 1)[-1])
                body = str(scm.pull(number).get("body") or "")
                if f"Programa {fid} (Nexo)" not in body:
                    scm.update_pr(number, {"body": body + note})
        finally:
            repo_ctx.close()
    return every


def status(ctx: AppContext, workspace: str, fid: str) -> dict[str, Any]:
    from nexo.store.runs import RunStore, project_key

    _, state = _state(ctx, workspace, fid)
    rows = []
    for name in state["order"]:
        info = state["repos"].get(name)
        if info is None:
            continue
        repo_ctx = AppContext(cwd=Path(info["path"]), home=ctx.home)
        try:
            store = RunStore(repo_ctx.conn, project_key(Path(info["path"])))
            stories = {sid: store.item_state(sid) or "Pronto" for sid in info["stories"].values()}
        finally:
            repo_ctx.close()
        rows.append({"repo": name, "feature": info["feature"], "stories": stories})
    done = all(s == "Concluido" for r in rows for s in r["stories"].values())
    return {"feature": fid, "order": state["order"], "repos": rows, "done": done}

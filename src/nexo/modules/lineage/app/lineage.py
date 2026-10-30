"""Linhagem automática na máquina de cada pessoa: sem CI, sem servidor e com as credenciais Git de quem está nela."""

from __future__ import annotations

import json
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexo.kernel.context import AppContext, nexo_config_home, nexo_home
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config
from nexo.kernel.runtime import yamlfile
from nexo.kernel.security.redaction import find
from nexo.kernel.workspace.specs import project_settings
from nexo.modules.lineage.adapters import gitread, scheduler
from nexo.modules.lineage.domain import model as m
from nexo.modules.lineage.domain.scan import SCANNER_VERSION, build_manifest

STALE_DAYS = 30
STATE = "lineage-state.json"


def _settings(ctx: AppContext) -> dict[str, Any]:
    return dict(config.resolved(ctx).values.get("lineage") or {})


def _team_dir(root: Path) -> tuple[str, Path]:
    settings = project_settings(root)
    return settings.team_name or "local", settings.team_dir()


def manifest_path(team_dir: Path, repo: str) -> Path:
    return team_dir / "lineage" / f"{repo}.yaml"


def repo_slug(root: Path) -> str:
    url = gitread.remote_url(root)
    name = url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git") if url else root.name
    return name or root.name


def _ref_name(root: Path) -> str:
    url = gitread.remote_url(root)
    if url.startswith("git@"):
        return "github:" + url.split(":", 1)[1].removesuffix(".git")
    if "github.com/" in url:
        return "github:" + url.split("github.com/", 1)[1].removesuffix(".git")
    return f"local:{repo_slug(root)}"


def scan(ctx: AppContext, root: Path | None = None, ref: str = "") -> dict[str, Any]:
    """Gera o manifesto de `origin/main`. O mesmo commit, com a mesma versão do scanner, gera sempre o mesmo."""
    target = root or ctx.project_root
    if target is None:
        raise NexoError("rode dentro de um repositório do projeto")
    team, _dir = _team_dir(target)
    branch = ref or gitread.default_ref(target)
    commit = gitread.head_of(target, branch)
    tree = gitread.tree(target, branch)
    component_file = tree.get(".nexo/component.yaml")
    declared = None
    if component_file:
        from ruamel.yaml import YAML

        declared = YAML(typ="safe", pure=True).load(component_file) or None
    cfg = _settings(ctx)
    env = tuple(cfg.get("environments") or ()) or None
    return build_manifest(team, _ref_name(target), commit, "", tree, repo_slug(target), declared, env)


def publish(ctx: AppContext, root: Path | None = None, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    """Grava no repositório do time. Nunca sobrescreve um manifesto mais novo com um antigo."""
    from nexo.kernel import services

    Store = services.get("demands.store", "Store")
    target = root or ctx.project_root
    if target is None:
        raise NexoError("rode dentro de um repositório do projeto")
    data = manifest or scan(ctx, target)
    team, team_dir = _team_dir(target)
    problems = m.validate_manifest(data, team)
    if {f.kind for f in find(json.dumps(m.without_commit(data)))} - {"email"}:
        problems.append("o manifesto contém dado sensível")
    if problems:
        raise NexoError("manifesto inválido: " + "; ".join(problems))
    store = Store(team_dir, project_settings(target).project, project_settings(target).shared)
    store.pull()
    path = manifest_path(team_dir, repo_slug(target))
    current = yamlfile.load(path) if path.is_file() else {}
    decision = _should_write(target, current, data)
    if decision:
        return {"repo": data["repo"], "commit": data["commit"], "published": False, "reason": decision}
    data = {**data, "generated_at": datetime.now(UTC).isoformat(timespec="seconds")}
    path.parent.mkdir(parents=True, exist_ok=True)
    yamlfile.dump(path, data)
    sent = store.push(f"nexo: linhagem de {repo_slug(target)} em {data['commit'][:8]}")
    ctx.audit("lineage.published", {"repo": data["repo"], "commit": data["commit"]})
    return {"repo": data["repo"], "commit": data["commit"], "published": True, **sent}


def _should_write(root: Path, current: dict[str, Any], new: dict[str, Any]) -> str:
    """Vazio se pode gravar. Várias máquinas podem tentar: o mesmo commit não duplica, e a atrasada não volta atrás."""
    old_commit = str(current.get("commit") or "")
    if not old_commit:
        return ""
    old_scanner = int(current.get("scanner") or 0)
    if old_commit == new["commit"]:
        return "" if int(new["scanner"]) > old_scanner else "já publicado"
    if int(new["scanner"]) < old_scanner:
        return "versão do scanner mais antiga"
    if gitread.is_ancestor(root, new["commit"], old_commit):
        return "commit mais antigo que o publicado"
    return ""


# ------------------------------------------------------------------ consolidação
def team_dirs(ctx: AppContext) -> list[Path]:
    """Repositórios de time disponíveis nesta máquina, mais a pasta local do projeto."""
    out = []
    folder = nexo_home() / "teams"
    if folder.is_dir():
        out += [p for p in sorted(folder.iterdir()) if p.is_dir()]
    if ctx.project_root is not None:
        _, here = _team_dir(ctx.project_root)
        if here.is_dir() and here not in out:
            out.append(here)
    return out


def sync_teams(ctx: AppContext) -> dict[str, Any]:
    """Traz os repositórios dos outros times, só leitura. O time é encontrado pela lista `lineage.teams`."""
    teams = dict(_settings(ctx).get("teams") or {})
    folder = nexo_home() / "teams"
    folder.mkdir(parents=True, exist_ok=True)
    done, failed = [], []
    for name, url in sorted(teams.items()):
        target = folder / str(name)
        try:
            if target.exists():
                gitread._git(target, "pull", "--quiet", "--ff-only")
            else:
                gitread._git(folder, "clone", "--quiet", "--depth", "1", str(url), str(name))
            done.append(name)
        except NexoError as exc:
            failed.append({"team": name, "error": exc.message})
    return {"synced": done, "failed": failed}


def _parse_time(value: str) -> float:
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return 0.0


def consolidate(ctx: AppContext, now: float | None = None) -> m.Graph:
    """Não há arquivo consolidado: cada leitor junta os manifestos. Por ser determinístico, todos chegam ao mesmo."""
    graph = m.Graph()
    moment = time.time() if now is None else now
    stale_days = int(_settings(ctx).get("stale_days") or STALE_DAYS)
    for folder in team_dirs(ctx):
        lineage = folder / "lineage"
        if not lineage.is_dir():
            continue
        expected = str(yamlfile.load(folder / "team.yaml").get("name") or "")
        for path in sorted(lineage.rglob("*.yaml")):
            data = dict(yamlfile.load(path))
            problems = m.validate_manifest(data, expected)
            if problems:
                graph.invalid.append({"file": path.name, "problem": "; ".join(problems)})
                continue
            stamp = _parse_time(str(data.get("generated_at") or ""))
            m.add_manifest(graph, data, stale=bool(stamp) and moment - stamp > stale_days * 86400)
    return graph


def show(ctx: AppContext, component: str) -> dict[str, Any]:
    graph = consolidate(ctx)
    cid = _resolve(graph, component)
    return {"component": cid, **m.neighbors(graph, cid), **graph.components.get(cid, {})}


def _resolve(graph: m.Graph, name: str) -> str:
    if name in graph.components:
        return name
    hits = [c for c in graph.components if c.endswith(f"/{name}")]
    if len(hits) == 1:
        return hits[0]
    if hits:
        raise NexoError(f"mais de um componente com esse nome: {', '.join(hits)}")
    raise NexoError(f"componente sem registro: {name} (veja `nexo lineage coverage`)")


def walk(ctx: AppContext, component: str, direction: str, depth: int) -> dict[str, Any]:
    graph = consolidate(ctx)
    cid = _resolve(graph, component)
    return {"component": cid, direction: m.walk(graph, cid, direction, depth)}


def impact(ctx: AppContext, target: str) -> dict[str, Any]:
    return m.impact(consolidate(ctx), target)


def coverage(ctx: AppContext) -> dict[str, Any]:
    data = m.coverage(consolidate(ctx))
    data["agents"] = agent_state(ctx)
    expected = list(_settings(ctx).get("repos") or [])
    if expected:
        published = {p.stem for folder in team_dirs(ctx) for p in (folder / "lineage").glob("*.yaml")}
        data["repos_without_manifest"] = sorted(set(expected) - published)
    return data


def export_obsidian(ctx: AppContext, folder: Path) -> dict[str, Any]:
    """Notas com [[links]], numa pasta fora do Git: o grafo aparece no Obsidian sem entrar na assinatura do cérebro."""
    graph = consolidate(ctx)
    folder.mkdir(parents=True, exist_ok=True)

    def slug(text: str) -> str:
        return text.replace("/", "-").replace(":", "-").replace(".", "-").lower()

    count = 0
    for cid in sorted(graph.components):
        data = m.neighbors(graph, cid)
        lines = [
            "---",
            f"id: {slug(cid)}",
            "type: component",
            f'title: "{cid}"',
            "---",
            "",
            f"# {cid}",
            "",
            "## Entradas",
        ]
        lines += [f"- [[{slug(r['channel'])}]] ← {r['component']} ({r['state']})" for r in data["entries"]] or [
            "- nenhuma conhecida"
        ]
        lines += ["", "## Saídas"]
        lines += [f"- [[{slug(r['channel'])}]] → {r['component']} ({r['state']})" for r in data["outputs"]] or [
            "- nenhuma conhecida"
        ]
        (folder / f"{slug(cid)}.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        count += 1
    for channel, data2 in sorted(m.by_channel(graph).items()):
        state = m.state_of(data2, graph.stale_teams)
        lines = [
            "---",
            f"id: {slug(channel)}",
            "type: channel",
            f'title: "{channel}"',
            f"state: {state}",
            "---",
            "",
            f"# {channel}",
            "",
        ]
        lines += [f"- publica: [[{slug(link.component)}]]" for link in data2["pub"]]
        lines += [f"- consome: [[{slug(link.component)}]]" for link in data2["sub"]]
        (folder / f"{slug(channel)}.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        count += 1
    return {"folder": str(folder), "notes": count}


# ------------------------------------------------------------------ agente local
def projects_file() -> Path:
    return nexo_config_home() / "projects.yaml"


def known_projects() -> list[Path]:
    path = projects_file()
    data = yamlfile.load(path) if path.is_file() else {}
    return [Path(str(p)) for p in data.get("projects") or []]


def register_project(root: Path) -> None:
    path = projects_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    current = [str(p) for p in known_projects()]
    if str(root) not in current:
        yamlfile.dump(path, {"projects": sorted([*current, str(root)])})


def _state_path() -> Path:
    return nexo_home() / STATE


def _load_state() -> dict[str, Any]:
    path = _state_path()
    return dict(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else {}


def _save_state(data: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


def agent_state(ctx: AppContext) -> dict[str, Any]:
    state = _load_state()
    return {
        "last_run": state.get("last_run", ""),
        "last_error": state.get("last_error", ""),
        "repos": state.get("repos", {}),
    }


def run_agent(ctx: AppContext, repo: Path | None = None) -> dict[str, Any]:
    """Uma passada: compara, baixa se mudou, escaneia e publica. Nunca falha para o usuário: registra e tenta depois."""
    state = _load_state()
    seen = dict(state.get("repos") or {})
    results = []
    errors = []
    for root in [repo.resolve()] if repo else known_projects():
        if not (root / ".git").exists():
            continue
        try:
            remote = gitread.remote_head(root)
            if not remote:
                errors.append(f"{root.name}: sem acesso ao repositório")
                continue
            if seen.get(str(root)) == f"{remote}:{SCANNER_VERSION}":
                results.append({"repo": root.name, "changed": False})
                continue
            gitread.fetch(root)
            out = publish(ctx, root)
            seen[str(root)] = f"{remote}:{SCANNER_VERSION}"
            results.append({"repo": root.name, "changed": True, **out})
        except (NexoError, OSError) as exc:
            errors.append(f"{root.name}: {getattr(exc, 'message', exc)}"[:300])
    _save_state(
        {
            "repos": seen,
            "last_run": datetime.now(UTC).isoformat(timespec="seconds"),
            "last_error": "; ".join(errors),
        }
    )
    return {"results": results, "errors": errors}


def _nexo_command() -> str:
    return shutil.which("nexo") or f"{sys.executable} -m nexo"


def install_agent(ctx: AppContext) -> dict[str, Any]:
    out = scheduler.install(_nexo_command())
    hooks = [scheduler.install_hook(root, _nexo_command()) for root in known_projects() if (root / ".git").is_dir()]
    if ctx.project_root is not None and (ctx.project_root / ".git").is_dir():
        hooks.append(scheduler.install_hook(ctx.project_root, _nexo_command()))
    ctx.audit("lineage.agent_installed", {"system": str(out["system"])})
    return {**out, "hooks": sorted({h for h in hooks if h})}


def uninstall_agent(ctx: AppContext) -> dict[str, Any]:
    out = scheduler.uninstall()
    for root in known_projects():
        scheduler.remove_hook(root)
    ctx.audit("lineage.agent_removed", {"system": str(out["system"])})
    return out


# ------------------------------------------------------------------ aviso entre times
def notify_consumers(ctx: AppContext, target: str, summary: str) -> dict[str, Any]:
    """Abre uma issue no repositório de cada time consumidor. Leitura basta para abrir a issue."""
    from nexo.kernel.runtime import http

    data = impact(ctx, target)
    teams = dict(_settings(ctx).get("teams") or {})
    token = ""
    from nexo.kernel.secrets import credentials

    token = credentials.get_secret("github") or ""
    sent, skipped = [], []
    for team in sorted({c["team"] for c in data["known_consumers"]}):
        url = str(teams.get(team) or "")
        if "github.com" not in url:
            skipped.append(team)
            continue
        repo = url.split("github.com", 1)[1].lstrip(":/").removesuffix(".git")
        http.request(
            "POST",
            f"https://api.github.com/repos/{repo}/issues",
            {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            {
                "title": f"Mudança em {target}",
                "body": f"{summary}\n\nAviso do Nexo: seu componente é um consumidor conhecido.",
            },
        )
        sent.append(team)
    ctx.audit("lineage.notified", {"target": target, "teams": len(sent)})
    return {"target": target, "notified": sent, "without_repo": skipped, "note": data["note"]}

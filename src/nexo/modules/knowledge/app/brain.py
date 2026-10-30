"""`nexo brain ...`: pull verificado, status, busca, proposta e ferramentas do time do Nexo (init/sign/keygen)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from nexo.kernel import brain as brain_adapter
from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError
from nexo.kernel.platform import config
from nexo.kernel.runtime.resources import embedded_dir


def _cfg(ctx: AppContext) -> dict[str, Any]:
    return dict(config.resolved(ctx).values.get("brain") or {})


def trusted_keys(ctx: AppContext) -> list[str]:
    return [str(k) for k in _cfg(ctx).get("trusted_keys") or []]


def require_signature(ctx: AppContext) -> bool:
    cfg = _cfg(ctx)
    if "require_signature" in cfg:
        return bool(cfg["require_signature"])
    return bool(trusted_keys(ctx))


def pull(ctx: AppContext) -> dict[str, Any]:
    cfg = _cfg(ctx)
    repo = str(cfg.get("repo") or "")
    if not repo:
        raise NexoError("repositório do cérebro não configurado (config brain.repo)")
    lock = ctx.home / "brain.lock"
    ctx.home.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + float(cfg.get("lock_wait_seconds", 120))
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue  # o outro pull acabou de terminar
            if age > 600:
                lock.unlink(missing_ok=True)  # trava abandonada (> 10 min)
            elif time.time() > deadline:
                raise NexoError("outro pull do cérebro está em andamento") from None
            else:
                time.sleep(0.5)  # aguarda o pull em segundo plano terminar
    os.close(fd)
    try:
        from nexo.modules.demands.app.tracker import github_token
        from nexo.modules.knowledge.app import content as knowledge_content

        token = github_token(ctx, required=False) if "github.com" in repo else None
        result = brain_adapter.pull(
            repo,
            ctx.brain_dir,
            trusted_keys(ctx),
            require_signature(ctx),
            str(cfg.get("ref", "main")),
            token,
            knowledge_content.validate_tree,
            frozenset(revoked_keys(ctx)),
        )
    except NexoError as exc:
        ctx.audit("brain.pull_failed", {"repo": repo, "error": exc.message})
        (ctx.home / "brain.last_pull").write_text(str(time.time()), encoding="utf-8")
        raise
    finally:
        lock.unlink(missing_ok=True)
    (ctx.home / "brain.last_pull").write_text(str(time.time()), encoding="utf-8")
    if result.ok:  # S-08: revogação só cresce — uma versão posterior não "desrevoga" chave
        _remember_revoked(ctx, brain_adapter.revoked_in(ctx.brain_dir))
    data = {"version": result.version, "signed": result.signed, "verified": result.ok, "reason": result.reason}
    ctx.audit("brain.pulled", {"repo": repo, **data})
    return data


def status(ctx: AppContext) -> dict[str, Any]:
    cached = brain_adapter.cached_status(ctx.brain_dir) or {}
    last = ctx.home / "brain.last_pull"
    return {
        "repo": _cfg(ctx).get("repo") or None,
        "present": ctx.brain_dir.is_dir(),
        "version": cached.get("version"),
        "signed": cached.get("signed", False),
        "verified": cached.get("verified", False),
        "reason": cached.get("reason", ""),
        "require_signature": require_signature(ctx),
        "trusted_keys": len(trusted_keys(ctx)),
        "last_pull": float(last.read_text(encoding="utf-8")) if last.is_file() else None,
    }


def check_update(ctx: AppContext) -> dict[str, Any]:
    """Versão local do cérebro e se o repositório tem algo mais novo (sem baixar). Rede fora: `unknown`."""
    cfg = _cfg(ctx)
    repo = str(cfg.get("repo") or "")
    local = (brain_adapter.cached_status(ctx.brain_dir) or {}).get("version")
    result: dict[str, Any] = {"repo": repo or None, "version": local, "update": False, "unknown": False}
    if not repo:
        return result
    from nexo.modules.demands.app.tracker import github_token

    try:
        token = github_token(ctx, required=False) if "github.com" in repo else None
    except NexoError:
        token = None
    remote = brain_adapter.remote_head(repo, str(cfg.get("ref", "main")), token)
    if not remote:
        return {**result, "unknown": True}
    here = brain_adapter.local_head(ctx.brain_dir)
    return {**result, "update": remote != here, "missing": not here}


def auto_pull_due(ctx: AppContext) -> bool:
    cfg = _cfg(ctx)
    if not cfg.get("repo") or not cfg.get("auto_pull", True):
        return False
    from nexo.modules.knowledge.app import content as knowledge_content

    if not knowledge_content.hook_enabled(ctx, "session_start"):  # desligado em org/hooks.yaml
        return False
    last = ctx.home / "brain.last_pull"
    interval = float(cfg.get("pull_interval_minutes", 60)) * 60
    return not last.is_file() or time.time() - float(last.read_text(encoding="utf-8") or 0) > interval


def spawn_background_pull(ctx: AppContext) -> None:
    """Pull em segundo plano no início da sessão — nunca atrasa o comando do dev."""
    from nexo.modules.execution.app.agents import nexo_command

    (ctx.home / "brain.last_pull").write_text(str(time.time()), encoding="utf-8")
    kwargs: dict[str, Any] = (
        {"start_new_session": True}
        if sys.platform != "win32"
        else {"creationflags": getattr(subprocess, "DETACHED_PROCESS", 0)}
    )
    subprocess.Popen(
        [*nexo_command(), "--quiet", "brain", "pull"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ},
        **kwargs,
    )


def search_roots(ctx: AppContext) -> list[Path]:
    roots = [embedded_dir("skills")]
    if ctx.brain_dir.is_dir():
        roots.append(ctx.brain_dir)
    if ctx.project_root and (ctx.project_root / ".nexo" / "extensions").is_dir():
        roots.append(ctx.project_root / ".nexo" / "extensions")
    personal = ctx.home / "extensions" / "personal"
    if personal.is_dir():
        roots.append(personal)
    return roots


def _visible(ctx: AppContext, root: Path, path: Path) -> bool:
    """O agente lê o global e o próprio projeto: lição de um projeto não chega ao prompt de outro."""
    from nexo.kernel.workspace.specs import project_settings
    from nexo.modules.knowledge.domain.notes import visible_to

    rel = path.relative_to(root).as_posix()
    if not rel.startswith("projects/"):
        return True
    project = project_settings(ctx.project_root).project if ctx.project_root else ""
    return visible_to(rel, project)


def search(ctx: AppContext, query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Busca por termos no conteúdo do cérebro, das extensões e das skills embutidas.

    Pontua pela cobertura dos termos da pergunta (quantos aparecem) e pela densidade (ocorrências por tamanho), com
    peso maior para o conteúdo mais específico: projeto e pessoal > cérebro da org/squad > embutido no binário."""
    terms = list(dict.fromkeys(t for t in re.findall(r"\w+", query.lower()) if len(t) > 2))
    roots = search_roots(ctx)
    weights = {roots[0]: 1.0, **{r: 1.5 for r in roots[1:]}}
    if ctx.project_root is not None:
        weights[ctx.project_root / ".nexo" / "extensions"] = 2.0
    weights[ctx.home / "extensions" / "personal"] = 2.0
    results: list[dict[str, Any]] = []
    for root in roots:
        for path in root.rglob("*.md"):
            if not _visible(ctx, root, path):
                continue
            if ".git" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            low = text.lower()
            counts = [low.count(t) for t in terms]
            coverage = sum(1 for c in counts if c)
            if not coverage:
                continue
            density = sum(counts) / (len(low.split()) / 100 + 1)
            first = next((line for line in text.splitlines() if any(t in line.lower() for t in terms)), "")
            results.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "title": path.parent.name,
                    "excerpt": first.strip()[:300],
                    "score": round((coverage * 10 + density) * weights.get(root, 1.0), 2),
                }
            )
    results.sort(key=lambda r: (-float(r["score"]), str(r["path"])))
    return results[:limit]


def create_issue(ctx: AppContext, title: str, body: str, labels: list[str]) -> str | None:
    """Issue no repositório do cérebro. Falha → fila `pending_sync` (tracker 'brain')."""
    import json

    from nexo.kernel.runtime import http
    from nexo.store.runs import now_iso

    repo = str(_cfg(ctx).get("github_repo") or "")
    if not repo:
        ctx.audit("brain.issue_skipped", {"title": title, "reason": "brain.github_repo não configurado"})
        return None
    try:
        from nexo.modules.demands.app.tracker import github_token

        token = github_token(ctx)
        resp = http.request(
            "POST",
            f"https://api.github.com/repos/{repo}/issues",
            {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            {"title": title, "body": body, "labels": labels},
        )
        url = str(resp.body.get("html_url", ""))
        ctx.audit("brain.issue", {"title": title, "url": url})
        return url
    except (NexoError, OSError) as exc:
        message = exc.message if isinstance(exc, NexoError) else str(exc)
        ctx.conn.execute(
            "INSERT INTO pending_sync(project, local_id, tracker, action, payload, error, attempts, created_at)"
            " VALUES (?,?,?,?,?,?,1,?)",
            (
                "brain",
                title[:80],
                "brain",
                "issue",
                json.dumps({"title": title, "body": body, "labels": labels}),
                message,
                now_iso(),
            ),
        )
        ctx.audit("brain.issue_queued", {"title": title, "error": message})
        return None


def retry_issues(ctx: AppContext) -> tuple[int, int]:
    import json

    rows = ctx.conn.execute("SELECT * FROM pending_sync WHERE tracker = 'brain' ORDER BY id").fetchall()
    ok = failed = 0
    for row in rows:
        data = json.loads(row["payload"])
        ctx.conn.execute("DELETE FROM pending_sync WHERE id = ?", (row["id"],))
        if create_issue(ctx, data["title"], data["body"], data["labels"]):
            ok += 1
        else:
            failed += 1
    return ok, failed


def propose(ctx: AppContext, path: Path, title: str | None) -> str | None:
    if not path.is_file():
        raise NexoError(f"{path} não existe")
    from nexo.kernel.security.redaction import find, redact

    content = path.read_text(encoding="utf-8", errors="replace")
    if {f.kind for f in find(content)} - {"email"}:
        raise NexoError("o arquivo contém segredo ou dado pessoal; remova antes de propor (RF-54)")
    body = f"Proposta enviada por `{ctx.actor}` via `nexo brain propose`.\n\n```\n{redact(content)[:6000]}\n```\n"
    return create_issue(ctx, title or f"Proposta: {path.name}", body, ["nexo", "proposta"])


def propose_clone(ctx: AppContext, clone: Path, source: Path, target: str, title: str) -> dict[str, Any]:
    """Proposta por PR: branch no clone de trabalho, push e PR. Sem permissão de escrita, cai para issue."""
    from nexo.modules.knowledge.app import notes

    result = notes.propose_pr(clone, source, target, title)
    pushed = subprocess.run(
        ["git", "push", "-q", "-u", "origin", result["branch"]],
        cwd=clone,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    result["pushed"] = pushed.returncode == 0
    if not result["pushed"]:
        result["issue"] = propose(ctx, source, title)
        return result
    repo = str(_cfg(ctx).get("github_repo") or "")
    if repo:
        from nexo.kernel import services
        from nexo.modules.demands.app.tracker import github_token

        scm = services.get("delivery.github", "GitHubScm")(repo, github_token(ctx))
        pr = scm.open_pr(result["branch"], title, f"Proposta de `{ctx.actor}`.")
        result["pr"] = pr.get("html_url") or pr.get("number")
    return result


def init_repo(target: Path) -> list[str]:
    target.mkdir(parents=True, exist_ok=True)
    created = brain_adapter.scaffold(target)
    if not (target / ".git").exists():
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=target, check=False)
    return created


def revoked_keys(ctx: AppContext) -> set[str]:
    """Chaves revogadas já vistas em cérebros verificados + as fixadas na âncora da org."""
    path = ctx.home / "revoked-keys.json"
    seen = set(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else set()
    return seen | {str(x) for x in (config.anchor() or {}).get("revoked_keys") or []}


def _remember_revoked(ctx: AppContext, fingerprints: set[str]) -> None:
    if not fingerprints:
        return
    path = ctx.home / "revoked-keys.json"
    merged = sorted(revoked_keys(ctx) | fingerprints)
    path.write_text(json.dumps(merged), encoding="utf-8")


def public_key(key: str) -> dict[str, str]:
    """Chave pública e impressão digital (para `brain.trusted_keys` e `org/security/revoked-signers.yaml`)."""
    from nexo.kernel.secrets import keyvault

    pub = (
        keyvault.public_key(keyvault.parse_ref(key)) if keyvault.is_ref(key) else brain_adapter.public_key_of(Path(key))
    )
    return {"public_key": pub, "fingerprint": brain_adapter.key_fingerprint(pub)}


def keygen(private_key: Path) -> str:
    if private_key.exists():
        raise NexoError(f"{private_key} já existe")
    return brain_adapter.keygen(private_key)


def sign(ctx: AppContext, path: Path, key: str, version: str) -> Path:
    from nexo.modules.knowledge.app import notes

    notes.require_clean_and_valid(path)
    from nexo.kernel.secrets import awskms, keyvault

    in_vault = keyvault.is_ref(key) or awskms.is_ref(key)
    if config.anchor() and not in_vault:  # S-08: no build corporativo, só cofre
        raise NexoError("build corporativo: o cérebro só é assinado com chave em cofre (--key awskms://… ou azkv://…)")
    manifest = brain_adapter.sign(path, key if in_vault else Path(key).expanduser(), version)
    ctx.audit("brain.signed", {"path": str(path), "version": version, "vault": in_vault})
    return manifest

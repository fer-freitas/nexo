"""Operações git do Nexo: worktrees por tarefa, checkpoints e squash."""

from __future__ import annotations

import subprocess
from pathlib import Path

from nexo.kernel.errors import NexoError

NX_IDENTITY = ("Nexo", "nexo@localhost")
_service: dict[str, str] = {}  # S-09: identidade de serviço e chave de assinatura dos commits do Nexo


def configure(name: str = "", email: str = "", signing_key: str = "") -> None:
    """Commits do Nexo assinados com chave SSH da identidade de serviço; vazio = comportamento anterior."""
    _service.clear()
    _service.update({k: v for k, v in {"name": name, "email": email, "key": signing_key}.items() if v})


class GitError(NexoError):
    pass


def git(args: list[str], cwd: Path, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args[:3])}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.strip()


def is_repo(path: Path) -> bool:
    return git(["rev-parse", "--is-inside-work-tree"], path, check=False) == "true"


def head(path: Path) -> str | None:
    out = git(["rev-parse", "--verify", "-q", "HEAD"], path, check=False)
    return out or None


_USERS: dict[str, tuple[str, str]] = {}


def _git_user(path: str) -> tuple[str, str]:
    """nome e e-mail do git consultados uma vez por repositório (eram dois processos a cada commit)."""
    if path not in _USERS:
        repo = Path(path)
        _USERS[path] = (
            git(["config", "user.name"], repo, check=False),
            git(["config", "user.email"], repo, check=False),
        )
    return _USERS[path]


def _identity_args(path: Path) -> list[str]:
    if _service.get("key"):
        name = _service.get("name") or NX_IDENTITY[0]
        email = _service.get("email") or NX_IDENTITY[1]
        return [
            "-c",
            f"user.name={name}",
            "-c",
            f"user.email={email}",
            "-c",
            "gpg.format=ssh",
            "-c",
            f"user.signingkey={_service['key']}",
            "-c",
            "commit.gpgsign=true",
        ]
    name, email = _git_user(str(path))
    args = ["-c", "commit.gpgsign=false"]
    if not name or not email:
        args += ["-c", f"user.name={NX_IDENTITY[0]}", "-c", f"user.email={NX_IDENTITY[1]}"]
    return args


def ensure_initial_commit(repo: Path) -> str:
    sha = head(repo)
    if sha:
        return sha
    git([*_identity_args(repo), "commit", "--allow-empty", "-m", "chore: commit inicial (nexo)"], repo)
    return head(repo) or ""


def branch_exists(repo: Path, branch: str) -> bool:
    return bool(git(["rev-parse", "--verify", "-q", f"refs/heads/{branch}"], repo, check=False))


def exclude_locally(workdir: Path, patterns: list[str]) -> None:
    """Padrões fora do git só nesta máquina (`info/exclude`, compartilhado pelos worktrees), sem mexer no
    .gitignore do time — `.venv/` e `*.egg-info/` criados pelo pip no preparo do ambiente."""
    common = git(["rev-parse", "--git-common-dir"], workdir, check=False).strip()
    if not common:
        return
    path = (workdir / common if not Path(common).is_absolute() else Path(common)) / "info" / "exclude"
    path.parent.mkdir(parents=True, exist_ok=True)
    current = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    missing = [p for p in patterns if p not in current]
    if missing:
        text = "\n".join([*current, "# Nexo: ambiente local (pip)", *missing]) + "\n"
        path.write_text(text, encoding="utf-8")


def add_worktree(repo: Path, path: Path, branch: str, base: str) -> Path:
    if path.exists() and (path / ".git").exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    git(["worktree", "prune"], repo, check=False)
    if branch_exists(repo, branch):
        git(["worktree", "add", str(path), branch], repo)
    else:
        git(["worktree", "add", "-b", branch, str(path), base], repo)
    return path


def checkpoint(worktree: Path, message: str) -> str:
    git(["add", "-A"], worktree)
    staged = git(["diff", "--cached", "--name-only"], worktree)
    if staged:
        git([*_identity_args(worktree), "commit", "-q", "--no-verify", "-m", message], worktree)
    return head(worktree) or ""


def changed_files(worktree: Path, base: str) -> list[str]:
    committed = git(["diff", "--name-only", f"{base}..HEAD"], worktree, check=False).splitlines()
    pending = git(["status", "--porcelain"], worktree, check=False).splitlines()
    files = set(committed) | {line[3:].strip() for line in pending if len(line) > 3}
    return sorted(f for f in files if f)


def squash(worktree: Path, base: str, message: str) -> str | None:
    """Colapsa os checkpoints em um único commit sobre `base`. Retorna o sha ou None se não houver mudança."""
    checkpoint(worktree, "nexo: checkpoint final")
    if not git(["diff", "--name-only", f"{base}..HEAD"], worktree, check=False):
        return None
    git(["reset", "--soft", base], worktree)
    git([*_identity_args(worktree), "commit", "-q", "--no-verify", "-m", message], worktree)
    return head(worktree)


def log_subjects(worktree: Path, base: str) -> list[str]:
    return git(["log", "--format=%s", f"{base}..HEAD"], worktree, check=False).splitlines()


def resolve(repo: Path, ref: str) -> str | None:
    out = git(["rev-parse", "--verify", "-q", f"{ref}^{{commit}}"], repo, check=False)
    return out or None


def discard_local_changes(worktree: Path) -> None:
    """Descarta alterações não commitadas (artefatos de build/teste) — só para worktrees exclusivas do Nexo."""
    git(["reset", "-q", "--hard", "HEAD"], worktree, check=False)
    git(["clean", "-q", "-fd", "--exclude=.nexo"], worktree, check=False)


def merge(worktree: Path, branch: str, message: str) -> list[str]:
    """Merge sem fast-forward; retorna a lista de arquivos em conflito (vazia = sucesso)."""
    proc = subprocess.run(
        ["git", *_identity_args(worktree), "merge", "--no-ff", "--no-edit", "-m", message, branch],
        cwd=worktree,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode == 0:
        return []
    conflicts = git(["diff", "--name-only", "--diff-filter=U"], worktree, check=False).splitlines()
    if not conflicts:
        raise GitError(f"merge de {branch} falhou: {proc.stderr.strip() or proc.stdout.strip()}")
    return conflicts


def conclude_merge(worktree: Path) -> str:
    git(["add", "-A"], worktree)
    git([*_identity_args(worktree), "commit", "-q", "--no-verify", "--no-edit"], worktree)
    return head(worktree) or ""


def abort_merge(worktree: Path) -> None:
    git(["merge", "--abort"], worktree, check=False)


def diff(worktree: Path, base: str, max_chars: int = 40_000) -> str:
    return git(["diff", f"{base}..HEAD"], worktree, check=False)[:max_chars]


def diff_lines(worktree: Path, base: str) -> int:
    out = git(["diff", "--numstat", f"{base}..HEAD"], worktree, check=False)
    total = 0
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            total += int(parts[0]) + int(parts[1])
    return total


def branch_head(repo: Path, branch: str) -> str | None:
    return resolve(repo, f"refs/heads/{branch}")


def is_ancestor(repo: Path, ref: str, of: str = "HEAD") -> bool:
    """`ref` já está contido em `of` (ex.: branch da história já mergeada na principal)."""
    proc = subprocess.run(["git", "merge-base", "--is-ancestor", ref, of], cwd=repo, capture_output=True, check=False)
    return proc.returncode == 0

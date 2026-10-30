"""Packs: conteúdo de terceiros adotado com commit fixado, hash e licença. Nunca lido da `main` em tempo de execução.

Skill é instrução executada com as permissões do agente, então entrar exige passar por aqui: diff contra o lock,
detector de injeção, lista de scripts (bloqueados por padrão) e licença. Nada é executado ao baixar.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexo.kernel.errors import IntegrityFailure, NexoError, PolicyViolation
from nexo.kernel.runtime import yamlfile
from nexo.kernel.runtime.resources import embedded_dir
from nexo.kernel.security.injection import scan

LOCK = "packs.lock.yaml"
SCRIPT_SUFFIXES = {".sh", ".bash", ".ps1", ".bat", ".cmd", ".py", ".js", ".ts", ".mjs", ".cjs", ".rb", ".pl", ".exe"}
ALLOWED_LICENSES = {"MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"}
MAX_GUIDANCE = 6000
# etapa do trabalho → skill adotada que orienta o agente
ROLE_SKILL = {
    "qa-author": "test-driven-development",
    "developer": "verification-before-completion",
    "fix": "systematic-debugging",
    "reviewer": "requesting-code-review",
}
ADAPTATION = (
    "Neste fluxo, o Nexo cria as worktrees, paraleliza as tarefas e abre o PR. Não use subagentes, não crie worktrees "
    "e não abra PR. Ignore passos da skill sobre isso; o restante vale integralmente."
)


def root() -> Path:
    return embedded_dir("packs")


def lock_data(base: Path | None = None) -> dict[str, Any]:
    return dict(yamlfile.load((base or root()) / LOCK))


def tree_hash(folder: Path) -> str:
    """Hash do conteúdo adotado. Normaliza o fim de linha, para ser igual no Windows e no macOS."""
    digest = hashlib.sha256()
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        digest.update(path.relative_to(folder).as_posix().encode("utf-8"))
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def verify(base: Path | None = None) -> list[str]:
    """Um byte diferente do lock é falha de integridade."""
    folder = base or root()
    problems = []
    for pack in lock_data(folder).get("packs") or []:
        target = folder / str(pack["id"])
        if not target.is_dir():
            problems.append(f"{pack['id']}: pasta ausente")
        elif tree_hash(target) != pack.get("tree_sha256"):
            problems.append(f"{pack['id']}: conteúdo difere do lock")
    return problems


def require_intact() -> None:
    problems = verify()
    if problems:
        raise IntegrityFailure("; ".join(problems))


@dataclass
class Review:
    scripts: list[str] = field(default_factory=list)
    suspicious: list[dict[str, str]] = field(default_factory=list)
    bad_skills: list[str] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__


def _files(folder: Path) -> dict[str, str]:
    return {
        p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in folder.rglob("*")
        if p.is_file() and ".git" not in p.relative_to(folder).parts
    }


def review(candidate: Path, current: Path | None = None) -> Review:
    """O que muda e o que preocupa, sem executar nada do que foi baixado."""
    out = Review()
    new = _files(candidate)
    old = _files(current) if current and current.is_dir() else {}
    out.added = sorted(set(new) - set(old))
    out.removed = sorted(set(old) - set(new))
    out.changed = sorted(k for k in set(new) & set(old) if new[k] != old[k])
    for rel in sorted(new):
        path = candidate / rel
        if path.suffix.lower() in SCRIPT_SUFFIXES:
            out.scripts.append(rel)
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for hit in scan(text):
            out.suspicious.append({"file": rel, "signal": hit})
        if path.name == "SKILL.md" and not _skill_ok(rel, text):
            out.bad_skills.append(rel)
    return out


def _skill_ok(rel: str, text: str) -> bool:
    from nexo.modules.packs.domain.skills import check_skill

    return not check_skill(rel, text)


def lint_all(base: Path | None = None) -> list[dict[str, str]]:
    """Confere o formato Agent Skills de tudo o que está adotado e das skills de fábrica."""
    from nexo.modules.packs.domain.skills import check_skill

    problems = []
    folders = [base or root(), embedded_dir("skills")]
    for folder in folders:
        for path in sorted(folder.rglob("SKILL.md")):
            rel = path.relative_to(folder).as_posix()
            for message in check_skill(rel, path.read_text(encoding="utf-8")):
                problems.append({"file": rel, "message": message})
    return problems


def _fetch(source: str, commit: str, target: Path) -> None:
    for args in (
        ["init", "-q"],
        ["remote", "add", "origin", source],
        ["fetch", "-q", "--depth", "1", "origin", commit],
    ):
        subprocess.run(["git", *args], cwd=target, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "-q", "FETCH_HEAD"], cwd=target, check=True, capture_output=True)


def _adopt(work: Path, target: Path, adopted: list[str], allow_scripts: bool) -> None:
    for item in adopted:
        src = work / item
        if not src.exists():
            raise NexoError(f"{item} não existe nesse commit")
        dest = target / item
        if src.is_dir():
            shutil.copytree(src, dest, dirs_exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
    license_file = work / "LICENSE"
    if license_file.is_file():
        shutil.copy2(license_file, target / "LICENSE")
    if not allow_scripts:
        for path in [p for p in target.rglob("*") if p.is_file() and p.suffix.lower() in SCRIPT_SUFFIXES]:
            path.unlink()


def update(
    pack_id: str,
    commit: str,
    dest: Path,
    source: str = "",
    adopted: list[str] | None = None,
    license_: str = "",
    allow_scripts: bool = False,
    accept_suspicious: bool = False,
) -> dict[str, Any]:
    """Prepara a adoção num clone do cérebro. Não mexe no que está em uso: quem aplica é o PR, depois de revisado."""
    lock = lock_data(dest) if (dest / LOCK).is_file() else {"packs": []}
    known: dict[str, Any] = next((p for p in lock["packs"] if p["id"] == pack_id), {})
    source = source or str(known.get("source", ""))
    adopted = adopted or list(known.get("adopted") or [])
    license_ = license_ or str(known.get("license", ""))
    if not source or not adopted:
        raise NexoError("informe --source e o que adotar (--adopt), ou atualize um pack que já está no lock")
    if license_ not in ALLOWED_LICENSES:
        raise PolicyViolation(f"licença não aprovada: {license_ or '(não informada)'}")
    if len(commit) != 40:
        raise PolicyViolation("fixe um commit completo: não se adota branch nem tag")
    with tempfile.TemporaryDirectory(prefix="nexo-pack-") as tmp:
        work = Path(tmp) / "src"
        work.mkdir()
        try:
            _fetch(source, commit, work)
        except subprocess.CalledProcessError as exc:
            raise NexoError(f"não foi possível baixar {source}@{commit}: {exc.stderr.decode()[:200]}") from exc
        candidate = Path(tmp) / "out"
        candidate.mkdir()
        _adopt(work, candidate, adopted, allow_scripts)
        report = review(candidate, dest / pack_id)
        if report.suspicious and not accept_suspicious:
            raise PolicyViolation(
                f"{pack_id}: conteúdo suspeito em {len(report.suspicious)} trecho(s). "
                "Revise e use --accept-suspicious, que fica na trilha"
            )
        target = dest / pack_id
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(candidate, target)
    entry = {
        "id": pack_id,
        "source": source,
        "commit": commit,
        "license": license_,
        "tree_sha256": tree_hash(target),
        "adopted": adopted,
        "rejected": dict(known.get("rejected") or {}),
    }
    lock["packs"] = [p for p in lock["packs"] if p["id"] != pack_id] + [entry]
    yamlfile.dump(dest / LOCK, lock)
    return {"pack": pack_id, "commit": commit, "review": report.as_dict()}


def listing() -> list[dict[str, Any]]:
    return [
        {
            "id": p["id"],
            "commit": str(p["commit"])[:12],
            "license": p["license"],
            "adopted": len(p.get("adopted") or []),
        }
        for p in lock_data().get("packs") or []
    ]


def show(pack_id: str) -> dict[str, Any]:
    pack = next((p for p in lock_data().get("packs") or [] if p["id"] == pack_id), None)
    if pack is None:
        raise NexoError(f"pack desconhecido: {pack_id}")
    return dict(pack)


def diff(pack_id: str) -> dict[str, Any]:
    """O que o conteúdo adotado tem de diferente do lock."""
    pack = show(pack_id)
    target = root() / pack_id
    return {
        "pack": pack_id,
        "commit": pack["commit"],
        "intact": target.is_dir() and tree_hash(target) == pack.get("tree_sha256"),
        "rejected": pack.get("rejected") or {},
    }


def skill_text(name: str) -> str:
    """Conteúdo da skill adotada, para orientar o agente na etapa."""
    for pack in lock_data().get("packs") or []:
        path = root() / str(pack["id"]) / "skills" / name / "SKILL.md"
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return ""


def guidance(role: str, task_kind: str = "") -> str:
    """A skill que orienta esta etapa. Escolha determinística: o Nexo decide, o agente segue."""
    name = ROLE_SKILL.get("fix" if task_kind in ("bug", "fix") else role)
    if not name:
        return ""
    text = skill_text(name)
    if not text:
        return ""
    if len(text) > MAX_GUIDANCE:
        text = text[:MAX_GUIDANCE].rsplit("\n", 1)[0]
    return f"\n\n## Método desta etapa ({name})\n{ADAPTATION}\n\n{text}"

"""Ciclo de vida do próprio Nexo: install, update, uninstall."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from nexo import __version__
from nexo.kernel.context import AppContext
from nexo.kernel.errors import IntegrityFailure, NexoError

# Chaves públicas Ed25519 (base64, 32 bytes) autorizadas a publicar releases.
# Vazio até o pipeline de release existir: `update` recusa qualquer artefato (falha segura).
TRUSTED_UPDATE_KEYS: tuple[str, ...] = ()

MAX_ARTIFACT_BYTES = 500 * 1024 * 1024


def install_mode() -> str:
    return "binary" if getattr(sys, "frozen", False) else "package"


def executable_name() -> str:
    return "nexo.exe" if os.name == "nt" else "nexo"


def platform_key() -> str:
    machine = platform.machine().lower()
    machine = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64"}.get(machine, machine)
    system = {"darwin": "macos", "win32": "windows", "linux": "linux"}.get(sys.platform, sys.platform)
    return f"{system}-{machine}"


def app_dir() -> Path:
    """Pasta da instalação standalone (PyInstaller onedir): executável + `_internal/`."""
    return Path(sys.executable).resolve().parent


def is_app_dir(path: Path) -> bool:
    return (path / executable_name()).is_file() and (path / "_internal").is_dir()


def _schedule_after_exit(path: Path, replacement: Path | None = None) -> None:
    """A instalação em uso não pode ser removida/trocada pelo próprio processo (Windows bloqueia e o
    bootloader do PyInstaller aborta): um processo destacado faz isso depois que o Nexo sair."""
    pid = os.getpid()
    if os.name == "nt":
        script = (
            f"Wait-Process -Id {pid} -ErrorAction SilentlyContinue; Remove-Item -Recurse -Force -LiteralPath '{path}'"
        )
        if replacement is not None:
            script += f"; Move-Item -LiteralPath '{replacement}' -Destination '{path}'"
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(["powershell", "-NoProfile", "-Command", script], creationflags=flags, close_fds=True)
        return
    script = 'while kill -0 "$1" 2>/dev/null; do sleep 0.2; done; rm -rf "$2"'
    args = [
        "sh",
        "-c",
        script + ('; mv "$3" "$2"' if replacement is not None else ""),
        "nexo-cleanup",
        str(pid),
        str(path),
    ]
    if replacement is not None:
        args.append(str(replacement))
    subprocess.Popen(
        args,
        start_new_session=True,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _is_running_install(path: Path) -> bool:
    return install_mode() == "binary" and path.resolve() == app_dir()


def ensure_home(ctx: AppContext) -> None:
    for sub in ("", "bin", "extensions/personal", "logs"):
        (ctx.home / sub).mkdir(parents=True, exist_ok=True)
    _ = ctx.conn  # cria/migra o banco


def install(ctx: AppContext, to: Path | None = None) -> Path | None:
    ensure_home(ctx)
    dest: Path | None = None
    if to is not None:
        if install_mode() != "binary":
            raise NexoError("--to só se aplica ao binário standalone")
        source = app_dir()
        if to.resolve() == source.resolve():
            raise NexoError("destino é a própria instalação atual")
        if to.exists():
            shutil.rmtree(to)
        shutil.copytree(source, to, symlinks=True)
        dest = to / executable_name()
        dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    ctx.audit("nexo.install", {"version": __version__, "mode": install_mode(), "dest": str(dest) if dest else None})
    return dest


def _safe_to_purge(home: Path) -> bool:
    resolved = home.resolve()
    forbidden = {Path(resolved.anchor), Path.home().resolve()}
    return resolved not in forbidden and len(resolved.parts) >= 3


def uninstall(ctx: AppContext, purge: bool) -> dict[str, Any]:
    result: dict[str, Any] = {"mode": install_mode(), "removed_binary": None, "purged": None}
    ctx.audit("nexo.uninstall", {"version": __version__, "purge": purge, "mode": install_mode()})
    if install_mode() == "binary":
        target = app_dir()
        if not is_app_dir(target):
            raise NexoError(f"recusado: {target} não parece uma instalação do Nexo")
        _schedule_after_exit(target)
        result["removed_binary"] = str(target)
    if purge:
        if not _safe_to_purge(ctx.home):
            raise NexoError(f"recusado: {ctx.home} não parece um diretório do Nexo")
        ctx.close()
        shutil.rmtree(ctx.home, ignore_errors=False)
        result["purged"] = str(ctx.home)
    return result


# ---------- update ----------


@dataclass(frozen=True)
class Artifact:
    version: str
    url: str
    sha256: str
    signature: str


def parse_version(value: str) -> tuple[int, ...]:
    core = value.split("-", 1)[0].split("+", 1)[0]
    try:
        return tuple(int(p) for p in core.split("."))
    except ValueError as exc:
        raise NexoError(f"versão inválida: {value}") from exc


def _fetch(source: str, limit: int) -> bytes:
    parsed = urllib.parse.urlparse(source)
    if parsed.scheme in ("", "file") or (len(parsed.scheme) == 1 and os.name == "nt"):
        path = Path(urllib.request.url2pathname(parsed.path) if parsed.scheme == "file" else source)
        data = path.read_bytes()
    elif parsed.scheme == "https":
        with urllib.request.urlopen(source, timeout=60) as resp:  # noqa: S310 — esquema validado acima
            data = resp.read(limit + 1)
    else:
        raise NexoError(f"origem recusada (somente https ou arquivo local): {source}")
    if len(data) > limit:
        raise NexoError("artefato excede o tamanho máximo permitido")
    return data


def read_manifest(source: str) -> Artifact:
    try:
        manifest = json.loads(_fetch(source, 1024 * 1024))
        entry = manifest["artifacts"][platform_key()]
        url = (
            urllib.parse.urljoin(source, entry["url"])
            if "://" in source
            else str((Path(source).parent / entry["url"]).resolve())
        )
        return Artifact(str(manifest["version"]), url, str(entry["sha256"]), str(entry["signature"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise NexoError(f"manifesto inválido ou sem artefato para {platform_key()}") from exc


def verify_artifact(data: bytes, artifact: Artifact, trusted_keys: tuple[str, ...]) -> None:
    if not trusted_keys:
        raise IntegrityFailure("nenhuma chave de publicação confiável configurada; atualização recusada")
    digest = hashlib.sha256(data).hexdigest()
    if digest != artifact.sha256.lower():
        raise IntegrityFailure("checksum SHA-256 não confere")
    signature = base64.b64decode(artifact.signature)
    for key in trusted_keys:
        try:
            Ed25519PublicKey.from_public_bytes(base64.b64decode(key)).verify(signature, data)
            return
        except (InvalidSignature, ValueError):
            continue
    raise IntegrityFailure("assinatura inválida ou de chave não confiável")


def _safe_extract(data: bytes, dest: Path) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for member in zf.infolist():
            resolved = (dest / member.filename).resolve()
            if not resolved.is_relative_to(dest.resolve()):
                raise IntegrityFailure(f"caminho inseguro no pacote: {member.filename}")
        zf.extractall(dest)  # membros validados acima
    exe = dest / executable_name()
    if exe.is_file():
        exe.chmod(0o755)


def replace_install(target: Path, data: bytes) -> None:
    """Troca a pasta da instalação pelo conteúdo do pacote (zip) já verificado."""
    staging = target.with_name(target.name + ".new")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    _safe_extract(data, staging)
    if not is_app_dir(staging):
        shutil.rmtree(staging)
        raise IntegrityFailure("pacote não contém uma instalação válida do Nexo")
    if _is_running_install(target):
        _schedule_after_exit(target, replacement=staging)
        return
    backup = target.with_name(target.name + ".old")
    if backup.exists():
        shutil.rmtree(backup)
    if target.exists():
        target.replace(backup)
    staging.replace(target)
    if backup.exists():
        shutil.rmtree(backup)


def update(
    ctx: AppContext,
    manifest_source: str,
    trusted_keys: tuple[str, ...] = TRUSTED_UPDATE_KEYS,
    target: Path | None = None,
) -> dict[str, Any]:
    artifact = read_manifest(manifest_source)
    if parse_version(artifact.version) <= parse_version(__version__):
        return {"updated": False, "version": __version__}
    data = _fetch(artifact.url, MAX_ARTIFACT_BYTES)
    try:
        verify_artifact(data, artifact, trusted_keys)
    except IntegrityFailure as exc:
        ctx.audit("nexo.update.rejected", {"version": artifact.version, "reason": exc.message})
        raise
    destination = target or (app_dir() if install_mode() == "binary" else None)
    if destination is None:
        return {"updated": False, "version": __version__, "package_mode": True, "available": artifact.version}
    replace_install(destination, data)
    ctx.audit("nexo.update", {"from": __version__, "to": artifact.version, "sha256": artifact.sha256})
    return {"updated": True, "version": artifact.version}

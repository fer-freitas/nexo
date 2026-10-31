"""Segundo cérebro (`nexo-brain`): pull com staging, manifesto assinado (Ed25519) e cache offline.

Manifesto `.brain/manifest.json`: {"version", "files": {caminho: sha256}, "signature"} — a assinatura cobre
o JSON canônico de `version` + `files`. Uma versão só substitui o cache se a assinatura e todos os hashes conferirem.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from nexo.kernel.errors import IntegrityFailure, NexoError

MANIFEST = ".brain/manifest.json"
SKIP = {".git", ".brain"}


@dataclass(frozen=True)
class Verification:
    ok: bool
    signed: bool
    version: str
    reason: str = ""


def _files(root: Path, meta_dir: str = ".brain") -> dict[str, str]:
    """Arquivos cobertos pela assinatura. `.obsidian/` e a lixeira dele nunca entram."""
    out: dict[str, str] = {}
    skip = {".git", meta_dir, ".obsidian", ".trash"}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if path.is_file() and rel.parts[0] not in skip:
            out[rel.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _canonical(version: str, files: dict[str, str]) -> bytes:
    return json.dumps({"version": version, "files": files}, sort_keys=True, separators=(",", ":")).encode()


def keygen(private_path: Path) -> str:
    key = Ed25519PrivateKey.generate()
    private_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    private_path.chmod(0o600)
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(pub).decode()


def normalize_key(trusted: str) -> str:
    """`es256:<der>` ou `ed25519:<raw>`; a forma antiga (só base64, 32 bytes) é Ed25519."""
    return trusted if trusted.startswith(("es256:", "ed25519:")) else f"ed25519:{trusted}"


def key_fingerprint(trusted: str) -> str:
    return hashlib.sha256(normalize_key(trusted).encode()).hexdigest()[:16]


def _load_public(trusted: str) -> tuple[str, Any]:
    kind, _, data = normalize_key(trusted).partition(":")
    raw = base64.b64decode(data)
    if kind == "es256":
        return kind, serialization.load_der_public_key(raw)
    return kind, Ed25519PublicKey.from_public_bytes(raw)


def public_key_of(private_path: Path) -> str:
    key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
    if isinstance(key, Ed25519PrivateKey):
        raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return base64.b64encode(raw).decode()
    if isinstance(key, ec.EllipticCurvePrivateKey) and isinstance(key.curve, ec.SECP256R1):
        der = key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        return "es256:" + base64.b64encode(der).decode()
    raise NexoError("chave privada precisa ser Ed25519 ou EC P-256")


def sign(root: Path, key_ref: Path | str, version: str, meta_dir: str = ".brain") -> Path:
    """Assina com chave local ou no AWS KMS.

    Serve para o cérebro e para qualquer pasta verificável (ex.: pacote de evidências, `meta_dir=".evidencia"`)."""
    files = _files(root, meta_dir)
    payload = _canonical(version, files)
    from nexo.kernel.secrets import awskms

    if isinstance(key_ref, str) and awskms.is_ref(key_ref):
        signature = awskms.sign_digest(key_ref, hashlib.sha256(payload).digest())
        alg, public = "es256", awskms.public_key(key_ref)
    else:
        path = Path(key_ref)
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if isinstance(key, Ed25519PrivateKey):
            signature, alg = key.sign(payload), "ed25519"
        elif isinstance(key, ec.EllipticCurvePrivateKey) and isinstance(key.curve, ec.SECP256R1):
            signature, alg = key.sign(payload, ec.ECDSA(hashes.SHA256())), "es256"
        else:
            raise NexoError("chave privada precisa ser Ed25519 ou EC P-256")
        public = public_key_of(path)
    manifest = root / meta_dir / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "version": version,
                "files": files,
                "alg": alg,
                "key_id": key_fingerprint(public),
                "signature": base64.b64encode(signature).decode(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return manifest


def write_manifest(root: Path, version: str, meta_dir: str) -> Path:
    """Manifesto só com hashes (sem assinatura): garante integridade; a autoria depende de uma assinatura."""
    manifest = root / meta_dir / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"version": version, "files": _files(root, meta_dir)}, indent=2), encoding="utf-8")
    return manifest


def check_manifest(root: Path, trusted_keys: list[str], meta_dir: str) -> dict[str, object]:
    """Integridade dos arquivos e, se houver, a assinatura do manifesto de uma pasta."""
    manifest_path = root / meta_dir / "manifest.json"
    if not manifest_path.is_file():
        return {"ok": False, "integrity": False, "signed": False, "reason": "pasta sem manifesto"}
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    files = dict(data.get("files") or {})
    actual = _files(root, meta_dir)
    if actual != files:
        changed = sorted({k for k in set(actual) | set(files) if actual.get(k) != files.get(k)})
        return {
            "ok": False,
            "integrity": False,
            "signed": bool(data.get("signature")),
            "reason": f"arquivos diferentes do manifesto: {', '.join(changed[:5])}",
        }
    if not data.get("signature"):
        return {"ok": True, "integrity": True, "signed": False, "reason": "sem assinatura: só integridade"}
    v = verify(root, trusted_keys, meta_dir=meta_dir)
    return {"ok": v.ok, "integrity": True, "signed": True, "reason": v.reason, "key_id": data.get("key_id")}


def verify(
    root: Path, trusted_keys: list[str], revoked: frozenset[str] = frozenset(), meta_dir: str = ".brain"
) -> Verification:
    manifest_path = root / meta_dir / "manifest.json"
    if not manifest_path.is_file():
        return Verification(False, False, _git_version(root), "cérebro sem manifesto assinado")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = str(data.get("version", ""))
    files = dict(data.get("files") or {})
    actual = _files(root, meta_dir)
    if actual != files:
        changed = sorted(set(actual.items()) ^ set(files.items()))
        return Verification(False, True, version, f"conteúdo difere do manifesto ({changed[0][0]} …)")
    live = [k for k in trusted_keys if key_fingerprint(k) not in revoked]
    if not live:
        reason = "chaves confiáveis revogadas" if trusted_keys else "nenhuma chave confiável configurada"
        return Verification(False, True, version, f"{reason} (brain.trusted_keys)")
    alg = str(data.get("alg") or "ed25519")
    signature = base64.b64decode(str(data.get("signature", "")))
    payload = _canonical(version, files)
    for trusted in live:
        try:
            kind, key = _load_public(trusted)
            if kind != alg:
                continue
            if kind == "es256":
                key.verify(signature, payload, ec.ECDSA(hashes.SHA256()))
            else:
                key.verify(signature, payload)
            return Verification(True, True, version)
        except (InvalidSignature, ValueError, TypeError):
            continue
    signer = str(data.get("key_id") or "")
    if signer and signer in revoked:
        return Verification(False, True, version, f"assinado com chave revogada ({signer})")
    return Verification(False, True, version, "assinatura inválida ou de chave não confiável")


def revoked_in(root: Path) -> set[str]:
    """Impressões digitais em `org/security/revoked-signers.yaml` (lista só cresce no cache local)."""
    path = root / "org" / "security" / "revoked-signers.yaml"
    if not path.is_file():
        return set()
    from nexo.kernel.runtime import yamlfile

    return {str(x) for x in yamlfile.load(path).get("revoked") or []}


def _git_version(root: Path) -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def local_head(root: Path) -> str:
    """Commit do cache local do cérebro (vazio se não houver)."""
    if not (root / ".git").exists():
        return ""
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=False, encoding="utf-8"
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""


def remote_head(repo: str, ref: str = "main", token: str | None = None, timeout: float = 8) -> str:
    """Commit atual do cérebro no repositório (`git ls-remote`, sem baixar nada). Vazio se não deu para saber."""
    auth: list[str] = []
    if token and repo.startswith("https://"):
        basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        auth = ["-c", f"http.extraHeader=Authorization: Basic {basic}"]  # só em memória, nunca gravado
    try:
        proc = subprocess.run(
            ["git", *auth, "ls-remote", repo, f"refs/heads/{ref}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.split()[0] if proc.returncode == 0 and proc.stdout.strip() else ""


def _rmtree(path: Path) -> None:
    """Remove o cache antigo; no Windows os objetos do git são somente leitura."""

    def writable(func: Callable[..., object], target: str, _exc: BaseException) -> None:
        Path(target).chmod(stat.S_IWRITE)
        func(target)

    shutil.rmtree(path, onexc=writable)


def pull(
    repo: str,
    target: Path,
    trusted_keys: list[str],
    require_signature: bool,
    ref: str = "main",
    token: str | None = None,
    validate: Callable[[Path], list[str]] | None = None,
    revoked: frozenset[str] = frozenset(),
) -> Verification:
    """Clona em staging, verifica e só então substitui o cache. Falha → cache anterior permanece."""
    staging_root = Path(tempfile.mkdtemp(prefix="nexo-brain-"))
    staging = staging_root / "brain"
    try:
        auth: list[str] = []
        if token and repo.startswith("https://"):
            basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            auth = ["-c", f"http.extraHeader=Authorization: Basic {basic}"]  # só em memória, nunca gravado
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # nunca travar pedindo senha
        proc = subprocess.run(
            ["git", *auth, "clone", "-q", "--depth", "1", "--branch", ref, repo, str(staging)],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
            env=env,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            raise NexoError(f"não foi possível baixar o cérebro: {proc.stderr.strip()[:300]}")
        result = verify(staging, trusted_keys, revoked)
        if not result.ok and (require_signature or (result.signed and "difere" in result.reason)):
            raise IntegrityFailure(f"cérebro recusado: {result.reason}")
        problems = validate(staging) if validate else []
        if problems:  # conteúdo fora do contrato ou incompatível: mantém o cache anterior
            raise IntegrityFailure("conteúdo do cérebro recusado: " + "; ".join(problems[:5]))
        backup = target.with_name(target.name + ".prev")
        if backup.exists():
            _rmtree(backup)
        if target.exists():
            target.replace(backup)
        staging.replace(target)
        if backup.exists():
            _rmtree(backup)
        (target / ".brain").mkdir(exist_ok=True)
        (target / ".brain" / "status.json").write_text(
            json.dumps(
                {"version": result.version, "signed": result.signed, "verified": result.ok, "reason": result.reason}
            ),
            encoding="utf-8",
        )
        return result
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def cached_status(target: Path) -> dict[str, object] | None:
    path = target / ".brain" / "status.json"
    if not path.is_file():
        path = target / ".brain" / "status.json"
    return dict(json.loads(path.read_text(encoding="utf-8"))) if path.is_file() else None


# O cérebro é editado à mão (inclusive pela org do cliente): nada de chave privada, segredo ou lixo de editor no git.
BRAIN_GITIGNORE = (
    "# Gerado pelo Nexo: o cérebro é assinado; segredo e lixo de sistema nunca entram\n"
    "*.key\n*.pem\n*.p12\n*.pfx\nid_rsa*\nid_ed25519*\n.env\n.env.*\n"
    ".DS_Store\nThumbs.db\ndesktop.ini\n.idea/\n.vscode/\n*.swp\n__pycache__/\n*.tmp\n"
)
SCAFFOLD: dict[str, str] = {
    ".gitignore": BRAIN_GITIGNORE,
    "README.md": "# nexo-brain\n\nSegundo cérebro da empresa para o Nexo. Mudanças só por PR aprovado (CODEOWNERS).\n",
    "CODEOWNERS": "* @time-nexo\n/org/security/ @seguranca @time-nexo\n/routing/ @seguranca @time-nexo\n"
    "/risk-map.yaml @seguranca @time-nexo\n",
    "org/config.yaml": "# Configuração da org (chaves em `locked` não podem ser sobrescritas)\nlocked: []\n",
    "org/standards/README.md": "# Padrões e nomenclaturas da empresa\n",
    "org/security/README.md": "# Políticas de segurança (policy.yaml sobrescreve a política embutida)\n",
    "squads/README.md": "# Configuração por squad: squads/<squad>/config.yaml\n",
    "routing/README.md": "# routing.yaml sobrescreve o catálogo de rotas embutido\n",
    "recipes/README.md": "# Receitas de qualidade por stack (sobrescrevem as embutidas)\n",
    "workflows/mappings/README.md": "# De-para com ferramentas de gestão\n",
    "prompts/README.md": "# Prompts dos papéis (sobrescrevem os embutidos)\n",
    "extensions/skills/README.md": "# Skills de escopo org\n",
    "extensions/hooks/README.md": "# Hooks de escopo org\n",
    "extensions/tools/README.md": "# Tools (MCP) de escopo org\n",
    "lessons/README.md": "# Lições aprovadas\n",
    "evals/golden-tasks/README.md": "# Tarefas de referência para avaliação\n",
}


def scaffold(target: Path) -> list[str]:
    created = []
    for rel, content in SCAFFOLD.items():
        path = target / rel
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            created.append(rel)
    return created

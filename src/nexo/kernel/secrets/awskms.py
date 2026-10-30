"""AWS KMS: assina o manifesto do cérebro sem a chave privada sair da AWS.

Referência: `awskms://<id ou alias da chave>`. Chave ECC_NIST_P256 com `kms:Sign` e `kms:GetPublicKey`, pela CLI e pela
autenticação da AWS da própria pessoa. Assim, a assinatura não depende de uma só pessoa nem de uma máquina pessoal.
"""

from __future__ import annotations

import base64
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from nexo.kernel.errors import NexoError
from nexo.kernel.runtime import system

PREFIX = "awskms://"


def is_ref(value: str) -> bool:
    return value.startswith(PREFIX) and len(value) > len(PREFIX)


def _key_id(ref: str) -> str:
    return ref[len(PREFIX) :]


def _call(*args: str) -> dict[str, Any]:
    exe = system.which("aws")
    if exe is None:
        raise NexoError("CLI da AWS não encontrada: é ela quem fala com o KMS")
    done = subprocess.run(
        [exe, "kms", *args, "--output", "json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if done.returncode != 0:
        raise NexoError(f"aws kms {args[0]} falhou: {done.stderr.strip()[:200]}")
    return dict(json.loads(done.stdout or "{}"))


def sign_digest(ref: str, digest: bytes) -> bytes:
    """Assina o SHA-256. O KMS já devolve em DER, o mesmo formato da chave local."""
    with tempfile.TemporaryDirectory(prefix="nexo-kms-", ignore_cleanup_errors=True) as tmp:
        path = Path(tmp) / "digest.bin"
        path.write_bytes(digest)
        answer = _call(
            "sign",
            "--key-id",
            _key_id(ref),
            "--message-type",
            "DIGEST",
            "--signing-algorithm",
            "ECDSA_SHA_256",
            "--message",
            f"fileb://{path.as_posix()}",
        )
    signature = str(answer.get("Signature") or "")
    if not signature:
        raise NexoError("resposta do KMS sem assinatura")
    return base64.b64decode(signature)


def public_key(ref: str) -> str:
    """Chave pública no formato de `brain.trusted_keys`: `es256:<SubjectPublicKeyInfo DER em base64>`."""
    answer = _call("get-public-key", "--key-id", _key_id(ref))
    if answer.get("KeySpec") != "ECC_NIST_P256":
        raise NexoError("a chave do KMS precisa ser ECC_NIST_P256 (ES256)")
    return "es256:" + str(answer.get("PublicKey") or "")

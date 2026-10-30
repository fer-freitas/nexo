"""Azure Key Vault / Managed HSM: assinatura do manifesto do cérebro sem a chave privada sair do cofre.

Referência: `azkv://<cofre>/<chave>[/<versão>]`. A pessoa ou o pipeline só precisa da permissão
`keys/sign` (e `keys/get` para ler a chave pública). Token: `NX_AZURE_KEYVAULT_TOKEN` (pipeline com identidade de
workload) ou `az account get-access-token --resource https://vault.azure.net`.
"""

from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from nexo.kernel.errors import NexoError
from nexo.kernel.runtime import http

API = "api-version=7.4"
REF = re.compile(
    r"^azkv://(?P<vault>[a-z0-9-]{3,24})(?:\.vault\.azure\.net)?/(?P<name>[A-Za-z0-9-]{1,127})"
    r"(?:/(?P<version>[A-Za-z0-9]{1,64}))?$"
)


@dataclass(frozen=True)
class KeyRef:
    vault: str
    name: str
    version: str = ""

    @property
    def url(self) -> str:
        suffix = f"/{self.version}" if self.version else ""
        return f"https://{self.vault}.vault.azure.net/keys/{self.name}{suffix}"


def is_ref(value: str) -> bool:
    return value.startswith("azkv://")


def parse_ref(value: str) -> KeyRef:
    m = REF.match(value)
    if not m:
        raise NexoError(f"referência de chave inválida: {value} (use azkv://<cofre>/<chave>[/<versão>])")
    return KeyRef(m["vault"], m["name"], m["version"] or "")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _token() -> str:
    token = os.environ.get("NX_AZURE_KEYVAULT_TOKEN", "").strip()
    if token:
        return token
    az = shutil.which("az")
    if az is None:
        raise NexoError("sem token do Key Vault: defina NX_AZURE_KEYVAULT_TOKEN ou faça `az login`")
    proc = subprocess.run(
        [
            az,
            "account",
            "get-access-token",
            "--resource",
            "https://vault.azure.net",
            "--query",
            "accessToken",
            "-o",
            "tsv",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise NexoError(f"não foi possível obter token do Key Vault: {proc.stderr.strip()[:200]}")
    return proc.stdout.strip()


def sign_raw(ref: KeyRef, digest: bytes, alg: str) -> bytes:
    """Assinatura crua do cofre (RS256: PKCS#1 v1.5; ES256: r‖s)."""
    answer = http.request(
        "POST",
        f"{ref.url}/sign?{API}",
        {"Authorization": f"Bearer {_token()}"},
        {"alg": alg, "value": _b64url(digest)},
    ).body
    return _unb64url(str(dict(answer or {}).get("value") or ""))


def sign_digest(ref: KeyRef, digest: bytes) -> bytes:
    """Assina o SHA-256 no cofre (ES256). Devolve a assinatura em DER (mesmo formato da chave local EC)."""
    raw = sign_raw(ref, digest, "ES256")
    if len(raw) != 64:
        raise NexoError("resposta do Key Vault sem assinatura ES256 válida")
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    return encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))


def public_key(ref: KeyRef) -> str:
    """Chave pública no formato de `brain.trusted_keys`: `es256:<SubjectPublicKeyInfo DER em base64>`."""
    answer = dict(http.request("GET", f"{ref.url}?{API}", {"Authorization": f"Bearer {_token()}"}).body or {})
    jwk = dict(answer.get("key") or {})
    if jwk.get("kty") not in ("EC", "EC-HSM") or jwk.get("crv") != "P-256":
        raise NexoError("a chave do cofre precisa ser EC P-256 (ES256)")
    numbers = ec.EllipticCurvePublicNumbers(
        int.from_bytes(_unb64url(str(jwk["x"])), "big"), int.from_bytes(_unb64url(str(jwk["y"])), "big"), ec.SECP256R1()
    )
    der = numbers.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return "es256:" + base64.b64encode(der).decode()

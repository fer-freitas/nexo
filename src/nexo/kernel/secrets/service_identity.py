"""Identidade de serviço: GitHub App no lugar de PAT pessoal.

JWT RS256 (chave privada no keyring `github_app_key`) e token de instalação.
"""

from __future__ import annotations

import base64
import json
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from nexo.kernel.errors import NexoError
from nexo.kernel.runtime import http

GITHUB_API = "https://api.github.com"
_cache: dict[str, tuple[str, float]] = {}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def app_jwt(app_id: str, key: str) -> str:
    """JWT do GitHub App (RS256, 9 min). `key`: PEM."""
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({"iat": now - 60, "exp": now + 540, "iss": str(app_id)}).encode())
    signing_input = f"{header}.{payload}".encode()
    private = serialization.load_pem_private_key(key.encode(), password=None)
    if not isinstance(private, rsa.RSAPrivateKey):
        raise NexoError("chave do GitHub App precisa ser RSA (PEM)")
    signature = private.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{payload}.{_b64url(signature)}"


def github_installation_token(app_id: str, installation_id: str, key: str) -> str:
    cache_key = f"gh:{app_id}:{installation_id}"
    cached = _cache.get(cache_key)
    if cached and cached[1] > time.time() + 300:
        return cached[0]
    body = http.request(
        "POST",
        f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
        {"Authorization": f"Bearer {app_jwt(app_id, key)}", "Accept": "application/vnd.github+json"},
        {},
    ).body
    token = str(dict(body or {}).get("token") or "")
    if not token:
        raise NexoError("GitHub não emitiu token de instalação para o App")
    _cache[cache_key] = (token, time.time() + 3300)
    return token

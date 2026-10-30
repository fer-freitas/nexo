"""Identidades de serviço: GitHub App e Service Principal do Azure DevOps no lugar de PAT pessoal.

- GitHub App: JWT RS256 (chave privada no keyring `github_app_key` ou no Key Vault `azkv://`) → token de instalação
  de 1 h, com permissões só dos repositórios da instalação.
- Azure DevOps: Service Principal do Entra ID (client credentials, segredo no keyring `azure_sp`) → token Bearer.
Tokens ficam só em memória durante o comando.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from nexo.kernel.errors import NexoError
from nexo.kernel.runtime import http
from nexo.kernel.secrets import keyvault

AZURE_DEVOPS_SCOPE = "499b84ac-1321-427f-aa17-267ca6975798/.default"  # recurso do Azure DevOps no Entra ID
GITHUB_API = "https://api.github.com"
_cache: dict[str, tuple[str, float]] = {}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def app_jwt(app_id: str, key: str) -> str:
    """JWT do GitHub App (RS256, 9 min). `key`: PEM ou referência `azkv://` a uma chave RSA do cofre."""
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({"iat": now - 60, "exp": now + 540, "iss": str(app_id)}).encode())
    signing_input = f"{header}.{payload}".encode()
    if keyvault.is_ref(key):
        signature = keyvault.sign_raw(keyvault.parse_ref(key), hashlib.sha256(signing_input).digest(), "RS256")
    else:
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


def azure_sp_token(tenant: str, client_id: str, secret: str, scope: str = AZURE_DEVOPS_SCOPE) -> str:
    cache_key = f"az:{tenant}:{client_id}:{scope}"
    cached = _cache.get(cache_key)
    if cached and cached[1] > time.time() + 300:
        return cached[0]
    body: Any = http.request(
        "POST",
        f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token",
        form={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret, "scope": scope},
    ).body
    token = str(dict(body or {}).get("access_token") or "")
    if not token:
        raise NexoError("Entra ID não emitiu token para o Service Principal")
    _cache[cache_key] = (token, time.time() + float(dict(body).get("expires_in", 3600)))
    return token

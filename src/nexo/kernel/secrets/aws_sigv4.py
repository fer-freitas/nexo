"""Assinatura AWS Signature Version 4, sem SDK: o suficiente para chamar o Amazon Bedrock pela API REST."""

from __future__ import annotations

import hashlib
import hmac
import urllib.parse
from datetime import UTC, datetime


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sign(
    method: str,
    url: str,
    headers: dict[str, str],
    payload: bytes,
    access_key: str,
    secret_key: str,
    region: str,
    service: str,
    session_token: str | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """Devolve os cabeçalhos a enviar (os recebidos + host, x-amz-date, token e Authorization)."""
    parts = urllib.parse.urlsplit(url)
    stamp = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    day = stamp[:8]
    out = {**headers, "host": parts.netloc, "x-amz-date": stamp}
    if session_token:
        out["x-amz-security-token"] = session_token
    canon = {k.lower().strip(): " ".join(str(v).split()) for k, v in out.items()}
    signed = ";".join(sorted(canon))
    query = "&".join(
        f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}"
        for k, v in sorted(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))
    )
    # Serviços que não são o S3 codificam o caminho de novo (o `%3A` do id do modelo vira `%253A`)
    path = urllib.parse.quote(parts.path or "/", safe="/~")
    request = "\n".join(
        [
            method.upper(),
            path,
            query,
            "".join(f"{k}:{canon[k]}\n" for k in sorted(canon)),
            signed,
            hashlib.sha256(payload).hexdigest(),
        ]
    )
    scope = f"{day}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(request.encode("utf-8")).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(f"AWS4{secret_key}".encode(), day), region), service), "aws4_request")
    signature = hmac.new(key, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    out["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={signature}"
    )
    return out

"""Cliente HTTP mínimo e injetável (somente HTTPS). Testes trocam o transporte por um simulado."""

from __future__ import annotations

import contextvars
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from nexo.kernel.errors import NexoError

TIMEOUT = 30
# chamadas às LLMs demoram mais que as APIs comuns; o prazo vale só durante aquela chamada (por thread).
_timeout: contextvars.ContextVar[float] = contextvars.ContextVar("nexo_http_timeout", default=TIMEOUT)


@dataclass
class Response:
    status: int
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


class HttpError(NexoError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


Transport = Callable[[str, str, dict[str, str], bytes | None], Response]


def _urllib_transport(method: str, url: str, headers: dict[str, str], data: bytes | None) -> Response:
    local = url.startswith(("http://127.0.0.1:", "http://localhost:"))  # serviços locais de desenvolvimento
    if not url.startswith("https://") and not local:
        raise NexoError(f"somente HTTPS é permitido: {url}")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310 — esquema validado
    try:
        with urllib.request.urlopen(req, timeout=_timeout.get()) as resp:  # noqa: S310
            raw = resp.read()
            return Response(resp.status, _decode(raw), dict(resp.headers))
    except urllib.error.HTTPError as exc:
        return Response(exc.code, _decode(exc.read()), dict(exc.headers or {}))
    except urllib.error.URLError as exc:
        raise NexoError(f"falha de rede em {url.split('?')[0]}: {exc.reason}") from exc


def _decode(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return raw.decode("utf-8", errors="replace")


_transport: Transport = _urllib_transport


def set_transport(transport: Transport | None) -> None:
    global _transport
    _transport = transport or _urllib_transport


def request(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    json_body: Any = None,
    content_type: str = "application/json",
    expect: tuple[int, ...] = (),
    form: dict[str, str] | None = None,
    timeout: float | None = None,
) -> Response:
    hdrs = {"Accept": "application/json", "User-Agent": "nexo", **(headers or {})}
    data = None
    if form is not None:  # OAuth (client credentials): corpo x-www-form-urlencoded
        import urllib.parse

        data = urllib.parse.urlencode(form).encode("utf-8")
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    elif json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        hdrs["Content-Type"] = content_type
    token = _timeout.set(timeout) if timeout else None
    try:
        resp = _transport(method, url, hdrs, data)
    finally:
        if token is not None:
            _timeout.reset(token)
    if (expect and resp.status not in expect) or (not expect and not resp.ok):
        detail = (
            (resp.body.get("message") or resp.body.get("detail"))
            if isinstance(resp.body, dict)
            else str(resp.body)[:300]
        )
        raise HttpError(resp.status, f"{method} {url.split('?')[0]}: {detail}")
    return resp

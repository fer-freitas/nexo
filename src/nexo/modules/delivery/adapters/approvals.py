"""Canais de aprovação externa e fontes externas de limite.

O sistema externo devolve só um sinal: `continue` ou `stop`. Canais:
- `file`: `<dir>/<id>.request.json` é escrito pelo Nexo; o processo externo grava `<dir>/<id>.decision`
  com `continue` ou `stop` (troca por arquivo, para empresas sem API);
- `http`: `POST <url>` com a solicitação; `GET <url>/<id>` → `{"status": "continue" | "stop" | "pending"}`;
- `servicenow`: requisição de mudança pela Table API; a aprovação dela é a decisão.
Limite externo do dev: arquivo YAML `{dev: {mês: limite}}` ou `GET <url>?dev=&month=` → `{"limit_brl", "tools"}`.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

from nexo.kernel.errors import NexoError
from nexo.kernel.runtime import http, yamlfile

DECISIONS = ("continue", "stop")


def _decision(text: str) -> str | None:
    value = text.strip().lower()
    return value if value in DECISIONS else None


class FileChannel:
    name = "file"

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def request(self, request_id: str, payload: dict[str, Any]) -> str:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"{request_id}.request.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)

    def poll(self, request_id: str) -> str | None:
        path = self.folder / f"{request_id}.decision"
        return _decision(path.read_text(encoding="utf-8")) if path.is_file() else None


class HttpChannel:
    name = "http"

    def __init__(self, url: str, token: str | None) -> None:
        if not url.startswith("https://"):
            raise NexoError("approvals.http.url deve usar https://")
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}

    def request(self, request_id: str, payload: dict[str, Any]) -> str:
        body = http.request("POST", self.url, self.headers, {"id": request_id, **payload}).body
        return str(body.get("ref") or body.get("id") or request_id)

    def poll(self, request_id: str) -> str | None:
        body = http.request("GET", f"{self.url}/{quote(request_id)}", self.headers).body
        return _decision(str(body.get("status") or ""))


class ServiceNowChannel:
    """ServiceNow: `change_request` pela Table API. Aprovado → continue; rejeitado → stop."""

    name = "servicenow"

    def __init__(self, instance_url: str, token: str, user: str | None = None) -> None:
        if not instance_url.startswith("https://"):
            raise NexoError("approvals.servicenow.instance_url deve usar https://")
        self.api = f"{instance_url.rstrip('/')}/api/now/table/change_request"
        auth = (
            "Basic " + base64.b64encode(f"{user}:{token}".encode()).decode() if user else f"Bearer {token}"
        )  # usuário de integração + senha, ou token OAuth
        self.headers = {"Authorization": auth}

    def open_change(self, fields: dict[str, Any]) -> dict[str, str]:
        body = http.request("POST", self.api, self.headers, fields).body
        rec = dict(body.get("result") or {})
        return {"ref": str(rec.get("sys_id", "")), "number": str(rec.get("number", ""))}

    def request(self, request_id: str, payload: dict[str, Any]) -> str:
        fields = {
            "short_description": f"[Nexo {request_id}] {payload.get('title', '')}"[:160],
            "description": json.dumps(payload, ensure_ascii=False, indent=2),
            "type": "normal",
            "correlation_id": request_id,
            "correlation_display": "nexo",
        }
        return self.open_change(fields)["ref"]

    def status(self, ref: str) -> dict[str, str]:
        rec = http.request("GET", f"{self.api}/{quote(ref)}?sysparm_fields=approval,state,number", self.headers).body
        rec = dict(rec.get("result") or {})
        return {
            "approval": str(rec.get("approval", "")),
            "state": str(rec.get("state", "")),
            "number": str(rec.get("number", "")),
        }

    def poll(self, ref: str) -> str | None:
        approval = self.status(ref)["approval"]
        return "continue" if approval == "approved" else "stop" if approval == "rejected" else None


def external_limit_file(path: Path, dev: str, month: str) -> dict[str, float] | None:
    """`{dev: {mês: 300}}` ou `{dev: {mês: {"*": 300, "devin": 100}}}` → limites por ferramenta ('*' = total)."""
    if not path.is_file():
        return None
    entry = ((yamlfile.load(path) or {}).get(dev) or {}).get(month)
    if entry is None:
        return None
    if isinstance(entry, dict):
        return {str(k): float(v) for k, v in entry.items()}
    return {"*": float(entry)}


def external_limit_http(url: str, token: str | None, dev: str, month: str) -> dict[str, float] | None:
    if not url.startswith("https://"):
        raise NexoError("budget.external.url deve usar https://")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    body = http.request("GET", f"{url.rstrip('/')}?dev={quote(dev)}&month={month}", headers).body
    if body.get("limit_brl") is None and not body.get("tools"):
        return None
    limits = {str(k): float(v) for k, v in (body.get("tools") or {}).items()}
    if body.get("limit_brl") is not None:
        limits["*"] = float(body["limit_brl"])
    return limits

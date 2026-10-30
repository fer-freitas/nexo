"""Servidor MCP mínimo sobre stdio (JSON-RPC 2.0 delimitado por linha).

Implementação própria e enxuta (sem SDK) para manter a superfície auditável: initialize, ping,
tools/list e tools/call. As tools são registradas pela camada de aplicação.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import IO, Any

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], dict[str, Any]]


class McpServer:
    def __init__(self, name: str, version: str, tools: list[ToolSpec]) -> None:
        self.name = name
        self.version = version
        self.tools = {t.name: t for t in tools}

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        msg_id = message.get("id")
        if msg_id is None:  # notificação
            return None
        try:
            if method == "initialize":
                requested = (message.get("params") or {}).get("protocolVersion")
                version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
                result: dict[str, Any] = {
                    "protocolVersion": version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": self.name, "version": self.version},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {
                    "tools": [
                        {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
                        for t in self.tools.values()
                    ]
                }
            elif method == "tools/call":
                result = self._call(message.get("params") or {})
            else:
                return _error(msg_id, -32601, f"método não suportado: {method}")
        except Exception as exc:  # erro interno vira erro JSON-RPC, nunca derruba o servidor
            return _error(msg_id, -32603, f"{type(exc).__name__}: {exc}")
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _call(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name"))
        tool = self.tools.get(name)
        if tool is None:
            return {"content": [{"type": "text", "text": f"tool desconhecida: {name}"}], "isError": True}
        args = params.get("arguments") or {}
        missing = [k for k in tool.input_schema.get("required", []) if k not in args]
        if missing:
            return {
                "content": [{"type": "text", "text": f"argumentos obrigatórios ausentes: {missing}"}],
                "isError": True,
            }
        try:
            payload = tool.handler(dict(args))
        except ValueError as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
        return {
            "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
            "structuredContent": payload,
            "isError": False,
        }

    def serve(self, stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> None:
        src = stdin or sys.stdin
        dst = stdout or sys.stdout
        for line in src:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                response: dict[str, Any] | None = _error(None, -32700, "JSON inválido")
            else:
                response = (
                    self.handle(message) if isinstance(message, dict) else _error(None, -32600, "requisição inválida")
                )
            if response is not None:
                dst.write(json.dumps(response, ensure_ascii=False) + "\n")
                dst.flush()


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

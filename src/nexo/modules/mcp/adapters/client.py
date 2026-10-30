"""Cliente MCP mínimo, para provar a conexão de verdade: inicializa, lista as ferramentas e chama uma de leitura."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from nexo.kernel.errors import NexoError
from nexo.kernel.runtime import http

PROTOCOL = "2025-03-26"


def _init_params() -> dict[str, Any]:
    return {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": {"name": "nexo", "version": "1"}}


class StdioClient:
    def __init__(self, command: str, args: list[str], env: dict[str, str] | None = None, timeout: float = 30) -> None:
        self.cmd = [command, *args]
        self.env = env
        self.timeout = timeout

    def exchange(self, tool: str = "", arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _init_params()},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        if tool:
            messages.append(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": arguments or {}},
                }
            )
        payload = "".join(json.dumps(m) + "\n" for m in messages)
        try:
            done = subprocess.run(
                self.cmd,
                input=payload,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                env=self.env,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NexoError(f"não foi possível iniciar o servidor MCP: {exc}") from exc
        replies = {}
        for line in done.stdout.splitlines():
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if "id" in data:
                replies[data["id"]] = data
        if 2 not in replies:
            raise NexoError(f"o servidor MCP não respondeu: {done.stderr.strip()[:200]}")
        return {"tools": [t["name"] for t in replies[2].get("result", {}).get("tools", [])], "call": replies.get(3)}


class HttpClient:
    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self.url = url
        self.headers = {"Accept": "application/json, text/event-stream", **(headers or {})}

    def _post(self, message: dict[str, Any]) -> Any:
        return http.request("POST", self.url, self.headers, message).body

    def exchange(self, tool: str = "", arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": _init_params()})
        listed = self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) or {}
        call = None
        if tool:
            call = self._post(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": arguments or {}},
                }
            )
        return {"tools": [t["name"] for t in (listed.get("result") or {}).get("tools", [])], "call": call}

from __future__ import annotations

import io
import json

from nexo.kernel.runtime.mcp_stdio import McpServer, ToolSpec


def server() -> McpServer:
    def echo(args: dict) -> dict:  # type: ignore[type-arg]
        if args.get("fail"):
            raise ValueError("falhou")
        if args.get("boom"):
            raise RuntimeError("boom")
        return {"echo": args["text"]}

    schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}
    return McpServer("nexo", "0.0.1", [ToolSpec("echo", "Eco", schema, echo)])


def call(srv: McpServer, method: str, params: dict | None = None, msg_id: int | None = 1) -> dict | None:  # type: ignore[type-arg]
    msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if msg_id is not None:
        msg["id"] = msg_id
    return srv.handle(msg)


def test_initialize_negotiates_version() -> None:
    res = call(server(), "initialize", {"protocolVersion": "2025-03-26"})
    assert res["result"]["protocolVersion"] == "2025-03-26"  # type: ignore[index]
    res = call(server(), "initialize", {"protocolVersion": "1999-01-01"})
    assert res["result"]["serverInfo"]["name"] == "nexo"  # type: ignore[index]


def test_list_and_call() -> None:
    srv = server()
    tools = call(srv, "tools/list")["result"]["tools"]  # type: ignore[index]
    assert tools[0]["name"] == "echo" and tools[0]["inputSchema"]["required"] == ["text"]
    res = call(srv, "tools/call", {"name": "echo", "arguments": {"text": "oi"}})["result"]  # type: ignore[index]
    assert res["structuredContent"] == {"echo": "oi"} and not res["isError"]


def test_errors() -> None:
    srv = server()
    assert call(srv, "tools/call", {"name": "nope"})["result"]["isError"]  # type: ignore[index]
    assert call(srv, "tools/call", {"name": "echo", "arguments": {}})["result"]["isError"]  # type: ignore[index]
    assert call(srv, "tools/call", {"name": "echo", "arguments": {"text": "x", "fail": 1}})["result"]["isError"]  # type: ignore[index]
    assert call(srv, "tools/call", {"name": "echo", "arguments": {"text": "x", "boom": 1}})["error"]["code"] == -32603  # type: ignore[index]
    assert call(srv, "resources/list")["error"]["code"] == -32601  # type: ignore[index]
    assert call(srv, "notifications/initialized", msg_id=None) is None
    assert call(srv, "ping") == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_serve_loop() -> None:
    lines = "\n".join(
        [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}),
            "not json",
            json.dumps([1, 2]),
            "",
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        ]
    )
    out = io.StringIO()
    server().serve(io.StringIO(lines), out)
    responses = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [r.get("error", {}).get("code") for r in responses] == [None, -32700, -32600]

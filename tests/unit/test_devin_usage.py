"""Consumo real do Devin a partir do banco local de sessões (sessions.db), com a estimativa como reserva.

A estrutura segue o banco real do Devin CLI 3000.11.3: totais de ACU e créditos em `sessions.metadata`, métricas de
tokens em `chat_message.metadata.metrics` e a mesma resposta repetida em vários nós (compactação/ramificação).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from nexo.modules.execution.adapters.agents.base import Patterns
from nexo.modules.execution.adapters.agents.generic import TextParser
from nexo.modules.usage.adapters import devin

SCHEMA = """
CREATE TABLE sessions (id TEXT PRIMARY KEY, working_directory TEXT NOT NULL, backend_type TEXT NOT NULL,
  model TEXT NOT NULL, agent_mode TEXT NOT NULL, created_at INTEGER NOT NULL, last_activity_at INTEGER NOT NULL,
  title TEXT, main_chain_id INTEGER, shell_last_seen_index INTEGER DEFAULT 0, cogs_json TEXT, workspace_dirs TEXT,
  hidden INTEGER NOT NULL DEFAULT 0, metadata TEXT);
CREATE TABLE message_nodes (row_id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, node_id INTEGER NOT NULL,
  parent_node_id INTEGER, chat_message TEXT NOT NULL, created_at INTEGER NOT NULL, metadata TEXT,
  UNIQUE(session_id, node_id));
"""


def response(request_id: str | None, tin: int, tout: int, acu: float | None = None) -> str:
    meta: dict[str, object] = {"num_tokens": tout, "request_id": request_id, "metrics": None}
    if request_id:
        meta["metrics"] = {
            "input_tokens": tin,
            "output_tokens": tout,
            "cache_read_tokens": 100,
            "cache_creation_tokens": None,
        }
    if acu is not None:
        meta["committed_acu_cost"] = acu
    return json.dumps({"message_id": "m", "role": "assistant", "content": "conteúdo sigiloso", "metadata": meta})


def make_db(path: Path, workdir: Path, start: int, session_meta: dict[str, float] | None) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    meta = json.dumps(session_meta) if session_meta is not None else None
    conn.execute(
        "INSERT INTO sessions (id, working_directory, backend_type, model, agent_mode, created_at, last_activity_at,"
        " cogs_json, metadata) VALUES (?,?,?,?,?,?,?,?,?)",
        ("s-1", str(workdir), "windsurf", "swe", "normal", start, start + 60, '[{"context": "sigiloso"}]', meta),
    )
    conn.execute(  # outra sessão na mesma pasta, horas antes: fora da janela
        "INSERT INTO sessions (id, working_directory, backend_type, model, agent_mode, created_at, last_activity_at,"
        " metadata) VALUES ('s-0',?,?,?,?,?,?,?)",
        (str(workdir), "windsurf", "swe", "normal", start - 9000, start - 8000, json.dumps({"total_acu_cost": 9.0})),
    )
    rows = [
        ("s-1", 0, response(None, 0, 0)),  # prompt de sistema: sem request_id
        ("s-1", 1, response("r-1", 981, 47, acu=0.5)),
        ("s-1", 2, response("r-1", 981, 47, acu=0.5)),  # mesma resposta repetida após compactação
        ("s-1", 3, response("r-2", 2000, 300, acu=1.25)),
        ("s-1", 4, "não é json"),
        ("s-0", 1, response("r-9", 5, 5)),
    ]
    for sid, node, raw in rows:
        conn.execute(
            "INSERT INTO message_nodes (session_id, node_id, chat_message, created_at) VALUES (?,?,?,?)",
            (sid, node, raw, start),
        )
    conn.commit()
    conn.close()
    return path


def test_session_totals_and_deduplicated_tokens(tmp_path: Path) -> None:
    wt = tmp_path / "worktree"
    wt.mkdir()
    now = time.time()
    db = make_db(tmp_path / "sessions.db", wt, int(now), {"total_credit_cost": 12, "total_acu_cost": 2.0})
    got = devin.read_session_usage(db, str(wt), now - 1, now + 30)
    assert got is not None and got.session_id == "s-1" and got.responses == 2
    assert got.units == {"acu": 2.0, "credits": 12.0, "tokens_in": 2981.0, "tokens_out": 347.0, "cache_read": 200.0}
    assert devin.read_session_usage(db, str(tmp_path / "outra"), now - 1, now + 30) is None
    assert devin.read_session_usage(db, str(wt), now + 3600, now + 7200) is None


def test_without_session_totals_sums_committed_acu(tmp_path: Path) -> None:
    wt = tmp_path / "worktree"
    wt.mkdir()
    now = time.time()
    db = make_db(tmp_path / "sessions.db", wt, int(now * 1000), None)  # carimbo em ms também é aceito
    got = devin.read_session_usage(db, str(wt), now - 1, now + 30)
    assert got is not None and got.units["acu"] == 1.75 and "credits" not in got.units


def test_missing_or_foreign_db_falls_back(tmp_path: Path) -> None:
    assert devin.read_session_usage(tmp_path / "nao-existe.db", "/x", 0, 1) is None
    other = tmp_path / "outro.db"
    sqlite3.connect(other).executescript("CREATE TABLE sessions (id TEXT);")
    assert devin.read_session_usage(other, "/x", 0, 1) is None


def test_candidate_paths_per_platform(tmp_path: Path) -> None:
    mac = tmp_path / ".local" / "share" / "devin" / "cli"
    win = tmp_path / ".devin"
    for d in (mac, win):
        d.mkdir(parents=True)
    (mac / "sessions.db").write_bytes(b"")
    (win / "cli_sessions.db").write_bytes(b"")
    assert devin.candidate_dbs(tmp_path, env={}) == [mac / "sessions.db", win / "cli_sessions.db"]
    assert devin.candidate_dbs(tmp_path, env={"NX_DEVIN_DB": "/tmp/x.db"}) == [Path("/tmp/x.db")]


def test_generic_adapter_uses_real_usage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wt = tmp_path / "worktree"
    wt.mkdir()
    spec = {"unit": "acu", "per_run": 1, "source": "devin_sessions_db"}
    parser = TextParser(Patterns({}), spec, "default", str(wt))
    db = make_db(tmp_path / "sessions.db", wt, int(parser.started), {"total_credit_cost": 12, "total_acu_cost": 2.0})
    monkeypatch.setenv("NX_DEVIN_DB", str(db))
    usage = {u.unit: u for u in parser.finish(0, "").usage}
    assert usage["acu"].quantity == 2.0 and not usage["acu"].estimated
    assert usage["tokens_in"].quantity == 2981 and "credits" not in usage
    monkeypatch.setenv("NX_DEVIN_DB", str(tmp_path / "vazio.db"))
    fallback = TextParser(Patterns({}), spec, "default", str(wt)).finish(0, "").usage
    assert [(u.unit, u.quantity, u.estimated) for u in fallback] == [("acu", 1.0, True)]

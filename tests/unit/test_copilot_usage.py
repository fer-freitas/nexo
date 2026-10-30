"""Consumo real do Copilot CLI a partir de ~/.copilot/session-store.db e cobrança por premium request ou AI Credits.

A estrutura segue o banco real do Copilot CLI 1.0.88 (tabela `assistant_usage_events`).
"""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from nexo.kernel.finops.costs import Pricing, UsageLine, usage_cost_brl
from nexo.modules.execution.adapters.agents.base import Patterns
from nexo.modules.execution.adapters.agents.generic import TextParser
from nexo.modules.usage.adapters import copilot

SCHEMA = """
CREATE TABLE sessions (id TEXT PRIMARY KEY, cwd TEXT, repository TEXT, host_type TEXT, branch TEXT, summary TEXT,
  created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now')));
CREATE TABLE turns (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, turn_index INTEGER NOT NULL,
  user_message TEXT, assistant_response TEXT, timestamp TEXT DEFAULT (datetime('now')));
CREATE TABLE assistant_usage_events (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
  turn_index INTEGER, agent_id TEXT, parent_tool_call_id TEXT, model TEXT NOT NULL, copilot_usage_model TEXT,
  input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER,
  reasoning_tokens INTEGER, total_nano_aiu INTEGER, request_multiplier REAL, duration_ms INTEGER,
  time_to_first_token_ms INTEGER, output_ttft_ms REAL, inter_token_latency_ms INTEGER, initiator TEXT,
  api_endpoint TEXT, reasoning_effort TEXT, finish_reason TEXT, content_filter_triggered INTEGER,
  token_details_json TEXT, created_at TEXT DEFAULT (datetime('now')));
"""


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def make_db(path: Path, workdir: Path, start: float) -> Path:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO sessions (id, cwd, created_at, updated_at) VALUES ('s-1',?,?,?)",
        (str(workdir), iso(start), iso(start + 30)),
    )
    conn.execute(
        "INSERT INTO sessions (id, cwd, created_at, updated_at) VALUES ('s-0',?,?,?)",
        (str(workdir), iso(start - 9000), iso(start - 8000)),
    )
    conn.execute("INSERT INTO turns (session_id, turn_index, user_message) VALUES ('s-1', 0, 'pedido sigiloso')")
    events = [  # (sessão, entrada, saída, cache, nano AIU, multiplicador, quem iniciou)
        ("s-1", 10810, 5, 1280, 193_760_000, 1.0, "user"),
        ("s-1", 20000, 400, 9000, 300_000_000, 1.0, "agent"),  # chamada do próprio agente: não é premium request
        ("s-1", 5000, 100, 0, 50_000_000, 0.33, "user"),
        ("s-0", 1, 1, 1, 9_000_000_000, 5.0, "user"),
    ]
    for sid, tin, tout, cache, nano, mult, who in events:
        conn.execute(
            "INSERT INTO assistant_usage_events (session_id, model, input_tokens, output_tokens, cache_read_tokens,"
            " cache_write_tokens, total_nano_aiu, request_multiplier, initiator) VALUES (?,?,?,?,?,0,?,?,?)",
            (sid, "mai-code-1.1-flash", tin, tout, cache, nano, mult, who),
        )
    conn.commit()
    conn.close()
    return path


def test_reads_premium_requests_ai_credits_and_tokens(tmp_path: Path) -> None:
    wt = tmp_path / "worktree"
    wt.mkdir()
    now = time.time()
    db = make_db(tmp_path / "session-store.db", wt, now)
    got = copilot.read_session_usage(db, str(wt), now - 1, now + 60)
    assert got is not None and got.session_id == "s-1" and got.responses == 3
    assert got.units == {
        "tokens_in": 35810.0,
        "tokens_out": 505.0,
        "cache_read": 10280.0,
        "premium_request": 1.33,
        "ai_credits": 0.54376,
    }
    assert copilot.read_session_usage(db, str(tmp_path / "outra"), now - 1, now + 60) is None
    assert copilot.read_session_usage(tmp_path / "nao-existe.db", str(wt), now, now) is None


def test_candidate_paths(tmp_path: Path) -> None:
    (tmp_path / ".copilot").mkdir()
    (tmp_path / ".copilot" / "session-store.db").write_bytes(b"")
    assert copilot.candidate_dbs(tmp_path, env={}) == [tmp_path / ".copilot" / "session-store.db"]
    assert copilot.candidate_dbs(tmp_path, env={"USERPROFILE": str(tmp_path)}) == [
        tmp_path / ".copilot" / "session-store.db"
    ]
    assert copilot.candidate_dbs(tmp_path, env={"NX_COPILOT_DB": "/x.db"}) == [Path("/x.db")]


def test_adapter_uses_real_usage_and_keeps_billing_unit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wt = tmp_path / "worktree"
    wt.mkdir()
    spec = {"unit": "premium_request", "per_run": 1, "source": "copilot_session_store"}
    parser = TextParser(Patterns({}), spec, "gpt-5", str(wt))
    monkeypatch.setenv("NX_COPILOT_DB", str(make_db(tmp_path / "session-store.db", wt, parser.started)))
    usage = [(u.unit, u.quantity, u.estimated) for u in parser.finish(0, "").usage]
    assert usage[0] == ("premium_request", 1.33, False)
    assert ("ai_credits", 0.54376, False) in usage and ("tokens_in", 35810.0, False) in usage


PRICING = Pricing.from_mapping(
    {
        "fx": [{"usd_brl": 5.0, "valid_from": "2026-01-01"}],
        "subscriptions": {"copilot": {"usd_month": 39, "unit_price_usd": 0.04, "valid_from": "2026-01-01"}},
    }
)


def test_copilot_cost_uses_billing_unit_not_tokens() -> None:
    line = UsageLine("s", "ana", "copilot", date(2026, 9, 25), "T-1", {"premium_request": 2.0, "tokens_in": 50000.0})
    assert usage_cost_brl(line, PRICING) == 0.4  # 2 × US$ 0,04 × 5,00; tokens não viram premium request
    credits = Pricing.from_mapping(
        {
            "fx": [{"usd_brl": 5.0, "valid_from": "2026-01-01"}],
            "subscriptions": {"copilot": {"usd_month": 39, "credit_price_usd": 0.01, "valid_from": "2026-01-01"}},
        }
    )
    line.units["ai_credits"] = 12.0
    assert usage_cost_brl(line, credits) == 0.6  # 12 créditos × US$ 0,01 × 5,00

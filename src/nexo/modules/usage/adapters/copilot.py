"""Consumo real do GitHub Copilot CLI a partir do banco local `session-store.db`, no lugar da estimativa por execução.

O Copilot CLI grava em `~/.copilot/` (Windows: `%USERPROFILE%\\.copilot\\`; `COPILOT_HOME` muda a pasta):
- `sessions`: id, `cwd`, `created_at`/`updated_at` (UTC, ISO);
- `assistant_usage_events`: uma linha por chamada ao modelo, com tokens, `request_multiplier`, `initiator` e
  `total_nano_aiu` (AI Credits × 10⁹).

Premium requests = soma do `request_multiplier` das chamadas iniciadas pelo usuário (as que o agente faz sozinho no
meio da tarefa não contam). O conteúdo da conversa (`turns`, `checkpoints`, índice de busca) não é lido.
Leitura em modo somente leitura; qualquer falha devolve None e vale a estimativa.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from nexo.modules.usage.adapters.devin import SLACK_S, SessionUsage, _norm

DB_NAME = "session-store.db"
NANO = 1_000_000_000


def candidate_dbs(home: Path | None = None, env: dict[str, str] | None = None) -> list[Path]:
    env = dict(os.environ if env is None else env)
    if env.get("NX_COPILOT_DB"):
        return [Path(env["NX_COPILOT_DB"])]
    home = home or Path.home()
    dirs = [Path(env["COPILOT_HOME"])] if env.get("COPILOT_HOME") else []
    dirs.append(home / ".copilot")
    if env.get("USERPROFILE"):
        dirs.append(Path(env["USERPROFILE"]) / ".copilot")
    out: list[Path] = []
    for d in dirs:
        p = d / DB_NAME
        if p.is_file() and not any(p.samefile(o) for o in out):
            out.append(p)
    return out


def _epoch(value: object) -> float:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return 0.0
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return 0.0
    if moment.tzinfo is None:  # `datetime('now')` do SQLite é UTC sem fuso
        moment = moment.replace(tzinfo=UTC)
    return moment.timestamp()


def read_session_usage(db: Path, workdir: str, started: float, ended: float) -> SessionUsage | None:
    """Consumo das sessões do Copilot que rodaram em `workdir` entre `started` e `ended` (epoch em segundos)."""
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None
    try:
        conn.row_factory = sqlite3.Row
        target = _norm(workdir)
        ids = [
            str(r["id"])
            for r in conn.execute("SELECT id, cwd, created_at, updated_at FROM sessions")
            if _norm(str(r["cwd"] or "")) == target
            and _epoch(r["created_at"]) <= ended + SLACK_S
            and _epoch(r["updated_at"]) >= started - SLACK_S
        ]
        if not ids:
            return None
        usage = SessionUsage(session_id=",".join(ids), db=str(db))
        units = {"tokens_in": 0.0, "tokens_out": 0.0, "cache_read": 0.0, "cache_write": 0.0}
        premium = 0.0
        nano = 0.0
        marks = ",".join("?" for _ in ids)
        columns = "input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, request_multiplier, initiator"
        sql = f"SELECT {columns}, total_nano_aiu FROM assistant_usage_events WHERE session_id IN ({marks})"  # noqa: S608
        rows = conn.execute(sql, ids)  # nosemgrep: só marcadores `?`; os ids vão como parâmetros
        for r in rows:
            usage.responses += 1
            units["tokens_in"] += float(r["input_tokens"] or 0)
            units["tokens_out"] += float(r["output_tokens"] or 0)
            units["cache_read"] += float(r["cache_read_tokens"] or 0)
            units["cache_write"] += float(r["cache_write_tokens"] or 0)
            if str(r["initiator"] or "") == "user":
                premium += float(r["request_multiplier"] or 0)
            nano += float(r["total_nano_aiu"] or 0)
        usage.units = {k: v for k, v in units.items() if v}
        usage.units["premium_request"] = premium
        if nano:
            usage.units["ai_credits"] = round(nano / NANO, 6)
        return usage
    except (sqlite3.Error, IndexError, KeyError):
        return None
    finally:
        conn.close()


def find_usage(workdir: str, started: float, ended: float, env: dict[str, str] | None = None) -> SessionUsage | None:
    for db in candidate_dbs(env=env):
        found = read_session_usage(db, workdir, started, ended)
        if found is not None:
            return found
    return None

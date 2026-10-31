"""Consumo de IA registrado localmente pelas ferramentas, inclusive sessões abertas fora do Nexo.

Varre, só para leitura e só os campos de consumo:
- Claude Code: `~/.claude/projects/*/*.jsonl` (`CLAUDE_CONFIG_DIR` muda a pasta) — `message.usage` de cada resposta,
  uma vez por `message.id` (a mesma resposta aparece em várias linhas quando tem vários blocos);
- Copilot CLI: `~/.copilot/session-store.db` › `assistant_usage_events`;
- Devin CLI: `sessions.db` › totais da sessão e métricas por resposta.

Devolve totais por ferramenta × sessão × dia × modelo, com a pasta da sessão só para separar o que o Nexo abriu
(a pasta nunca sai da máquina). Texto de prompts e respostas não é lido.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexo.modules.usage.adapters import copilot, devin


@dataclass
class SessionDay:
    tool: str
    session: str
    day: str  # AAAA-MM-DD (UTC)
    model: str
    cwd: str
    units: dict[str, float] = field(default_factory=dict)
    responses: int = 0

    def add(self, unit: str, value: float) -> None:
        if value:
            self.units[unit] = self.units.get(unit, 0.0) + value


def _day(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%d")


def _num(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


# ------------------------------------------------------------------ Claude Code


def claude_dirs(home: Path | None = None, env: dict[str, str] | None = None) -> list[Path]:
    env = dict(os.environ if env is None else env)
    base = Path(env["CLAUDE_CONFIG_DIR"]) if env.get("CLAUDE_CONFIG_DIR") else (home or Path.home()) / ".claude"
    projects = base / "projects"
    return [projects] if projects.is_dir() else []


def scan_claude(projects: Path, since: float = 0.0) -> list[SessionDay]:
    """Arquivos alterados desde `since` são relidos inteiros (os totais do dia são recalculados, não somados)."""
    out: dict[tuple[str, str, str], SessionDay] = {}
    for path in sorted(projects.glob("*/*.jsonl")):
        try:
            if path.stat().st_mtime < since:
                continue
        except OSError:
            continue
        seen: set[str] = set()
        for entry in _jsonl(path):
            if entry.get("type") != "assistant":
                continue
            message = entry.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("usage"), dict):
                continue
            mid = str(message.get("id") or entry.get("requestId") or entry.get("uuid") or "")
            if not mid or mid in seen:
                continue
            seen.add(mid)
            usage = message["usage"]
            tin, tout = _num(usage.get("input_tokens")), _num(usage.get("output_tokens"))
            cread, cwrite = _num(usage.get("cache_read_input_tokens")), _num(usage.get("cache_creation_input_tokens"))
            if not (tin or tout or cread or cwrite):
                continue  # mensagens sintéticas e erros de API não consomem
            stamp = str(entry.get("timestamp") or "")
            try:
                day = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(UTC).strftime("%Y-%m-%d")
            except ValueError:
                continue
            session = str(entry.get("sessionId") or entry.get("session_id") or path.stem)
            model = str(message.get("model") or "?")
            row = out.setdefault(
                (session, day, model), SessionDay("claude", session, day, model, str(entry.get("cwd") or ""))
            )
            row.responses += 1
            for unit, value in (
                ("tokens_in", tin),
                ("tokens_out", tout),
                ("cache_read", cread),
                ("cache_write", cwrite),
            ):
                row.add(unit, value)
    return list(out.values())


def _jsonl(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"assistant"' not in line:  # evita decodificar linhas que não interessam
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                if isinstance(data, dict):
                    yield data
    except OSError:
        return


# ------------------------------------------------------------------ Copilot CLI


def scan_copilot(db: Path) -> list[SessionDay]:
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return []
    try:
        conn.row_factory = sqlite3.Row
        cwd = {str(r["id"]): str(r["cwd"] or "") for r in conn.execute("SELECT id, cwd FROM sessions")}
        out: dict[tuple[str, str, str], SessionDay] = {}
        rows = conn.execute(
            "SELECT session_id, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,"
            " request_multiplier, initiator, total_nano_aiu, created_at FROM assistant_usage_events"
        )
        for r in rows:
            day = _day(copilot._epoch(r["created_at"]))
            key = (str(r["session_id"]), day, str(r["model"] or "?"))
            row = out.setdefault(key, SessionDay("copilot", key[0], day, key[2], cwd.get(key[0], "")))
            row.responses += 1
            row.add("tokens_in", _num(r["input_tokens"]))
            row.add("tokens_out", _num(r["output_tokens"]))
            row.add("cache_read", _num(r["cache_read_tokens"]))
            row.add("cache_write", _num(r["cache_write_tokens"]))
            if str(r["initiator"] or "") == "user":
                row.add("premium_request", _num(r["request_multiplier"]))
            row.add("ai_credits", _num(r["total_nano_aiu"]) / copilot.NANO)
        return list(out.values())
    except sqlite3.Error:
        return []
    finally:
        conn.close()


# ------------------------------------------------------------------ Devin CLI


def scan_devin(db: Path) -> list[SessionDay]:
    """Uma linha por sessão, no dia da última atividade (o total de ACU é da sessão, não por dia)."""
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return []
    try:
        conn.row_factory = sqlite3.Row
        present = {str(c[1]) for c in conn.execute("PRAGMA table_info(sessions)")}
        cols = [c for c in ("id", "working_directory", "model", "last_activity_at", "metadata") if c in present]
        out: list[SessionDay] = []
        for s in conn.execute(f"SELECT {', '.join(cols)} FROM sessions"):  # noqa: S608  # nosemgrep -- colunas fixas
            one = _devin_one(conn, s)
            row = SessionDay(
                "devin",
                str(s["id"]),
                _day(devin._seconds(s["last_activity_at"])),
                str(s["model"] or "?") if "model" in cols else "?",
                str(s["working_directory"]),
                one.units,
                one.responses,
            )
            out.append(row)
        return out
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def _devin_one(conn: sqlite3.Connection, s: sqlite3.Row) -> devin.SessionUsage:
    usage = devin.SessionUsage(session_id=str(s["id"]), db="")
    meta = devin._json(s["metadata"]) if "metadata" in s.keys() else {}  # noqa: SIM118 (sqlite3.Row)
    acu = devin._num(meta.get("total_acu_cost"))
    committed = 0.0
    seen: set[str] = set()
    for (raw,) in conn.execute("SELECT chat_message FROM message_nodes WHERE session_id = ?", (s["id"],)):
        found = devin._json(raw).get("metadata")
        if not isinstance(found, dict) or not found.get("request_id") or found["request_id"] in seen:
            continue
        seen.add(str(found["request_id"]))
        usage.responses += 1
        committed += devin._num(found.get("committed_acu_cost")) or 0.0
        metrics = found.get("metrics") if isinstance(found.get("metrics"), dict) else {}
        for key, unit in devin.TOKEN_KEYS.items():
            value = devin._num(metrics.get(key))  # type: ignore[union-attr]
            if value:
                usage.units[unit] = usage.units.get(unit, 0.0) + value
    usage.units["acu"] = acu if acu is not None else committed
    return usage


def scan_all(since: float = 0.0, env: dict[str, str] | None = None) -> list[SessionDay]:
    rows: list[SessionDay] = []
    for projects in claude_dirs(env=env):
        rows += scan_claude(projects, since)
    for db in copilot.candidate_dbs(env=env):
        rows += scan_copilot(db)
    for db in devin.candidate_dbs(env=env):
        rows += scan_devin(db)
    return rows

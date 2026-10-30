"""Consumo real do Devin CLI a partir do banco local de sessões (SQLite), no lugar da estimativa por execução.

O Devin grava as sessões em `sessions.db` (antes `cli_sessions.db`):
- macOS/Linux: `$XDG_DATA_HOME/devin/cli/` (padrão `~/.local/share/devin/cli/`); installs antigos em `cognition/` ou
  `chisel/`;
- Windows: `C:/Users/<usuário>/.devin/`.

A sessão do passo é achada pela pasta de trabalho (cada tarefa do Nexo roda no seu worktree) e pela janela de tempo.
Só são lidos os totais de ACU e créditos da sessão e as métricas de tokens das respostas; o conteúdo da conversa
não é extraído (a coluna `cogs_json`, que guarda contexto, nem é lida). Leitura em modo somente leitura; qualquer
falha devolve None e vale a estimativa.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DB_NAMES = ("sessions.db", "cli_sessions.db")
# métricas por resposta do modelo (`chat_message.metadata.metrics`) → unidade gravada pelo Nexo
TOKEN_KEYS = {
    "input_tokens": "tokens_in",
    "output_tokens": "tokens_out",
    "cache_read_tokens": "cache_read",
    "cache_creation_tokens": "cache_write",
}
SLACK_S = 5.0  # tolerância entre o relógio do passo e os carimbos do Devin


@dataclass
class SessionUsage:
    """Consumo de uma ou mais sessões de uma ferramenta de IA, lido do banco local dela."""

    session_id: str
    db: str
    units: dict[str, float] = field(default_factory=dict)
    responses: int = 0


DevinUsage = SessionUsage


def candidate_dbs(home: Path | None = None, env: dict[str, str] | None = None) -> list[Path]:
    """Bancos possíveis, do mais provável ao menos; `NX_DEVIN_DB` força um caminho."""
    env = dict(os.environ if env is None else env)
    home = home or Path.home()
    if env.get("NX_DEVIN_DB"):
        return [Path(env["NX_DEVIN_DB"])]
    data = Path(env["XDG_DATA_HOME"]) if env.get("XDG_DATA_HOME") else home / ".local" / "share"
    dirs = [data / ns / "cli" for ns in ("devin", "cognition", "chisel")]
    dirs += [home / ".devin", home / ".devin" / "cli", home / ".Devin", home / ".Devin" / "cli"]
    if env.get("LOCALAPPDATA"):
        dirs.append(Path(env["LOCALAPPDATA"]) / "devin" / "cli")
    out: list[Path] = []
    for d in dirs:
        for name in DB_NAMES:
            p = d / name
            if p.is_file() and not any(p.samefile(o) for o in out):  # `.devin` e `.Devin` podem ser a mesma pasta
                out.append(p)
    return out


def _norm(path: str) -> str:
    try:
        text = str(Path(path).resolve())
    except OSError:
        text = path
    text = text.rstrip("/\\")
    return text.lower() if sys.platform == "win32" else text


def _seconds(value: Any) -> float:
    v = float(value or 0)
    return v / 1000.0 if v > 1e11 else v  # Devin grava em ms; aceita s


def _json(value: Any) -> dict[str, Any]:
    try:
        data = json.loads(value) if value else {}
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def read_session_usage(db: Path, workdir: str, started: float, ended: float) -> DevinUsage | None:
    """Consumo da sessão do Devin que rodou em `workdir` entre `started` e `ended` (epoch em segundos).

    ACU e créditos: totais da sessão (`sessions.metadata.total_acu_cost|total_credit_cost`); sem eles, a soma de
    `committed_acu_cost` das respostas. Tokens: `metadata.metrics` de cada resposta, uma vez por `request_id` (a mesma
    resposta aparece em vários nós quando a conversa é compactada ou ramificada).
    """
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=2)
    except sqlite3.Error:
        return None
    try:
        conn.row_factory = sqlite3.Row
        target = _norm(workdir)
        present = {str(c[1]) for c in conn.execute("PRAGMA table_info(sessions)")}
        cols = [c for c in ("id", "working_directory", "created_at", "last_activity_at", "metadata") if c in present]
        matches = [
            r
            for r in conn.execute(f"SELECT {', '.join(cols)} FROM sessions")  # noqa: S608  # nosemgrep: colunas fixas
            if _norm(str(r["working_directory"])) == target
            and _seconds(r["created_at"]) <= ended + SLACK_S
            and _seconds(r["last_activity_at"]) >= started - SLACK_S
        ]
        if not matches:
            return None
        usage = DevinUsage(session_id=",".join(str(r["id"]) for r in matches), db=str(db))
        acu_total: float | None = None
        credits_total: float | None = None
        for r in matches:
            meta = _json(r["metadata"]) if "metadata" in cols else {}
            acu = _num(meta.get("total_acu_cost"))
            credits = _num(meta.get("total_credit_cost"))
            if acu is not None:
                acu_total = (acu_total or 0.0) + acu
            if credits is not None:
                credits_total = (credits_total or 0.0) + credits
        seen: set[str] = set()
        committed = 0.0
        for r in matches:
            for (raw,) in conn.execute("SELECT chat_message FROM message_nodes WHERE session_id = ?", (r["id"],)):
                found = _json(raw).get("metadata")
                if not isinstance(found, dict) or not found.get("request_id") or found["request_id"] in seen:
                    continue
                seen.add(str(found["request_id"]))
                usage.responses += 1
                committed += _num(found.get("committed_acu_cost")) or 0.0
                raw_metrics = found.get("metrics")
                metrics: dict[str, Any] = raw_metrics if isinstance(raw_metrics, dict) else {}
                for key, unit in TOKEN_KEYS.items():
                    value = _num(metrics.get(key))
                    if value:
                        usage.units[unit] = usage.units.get(unit, 0.0) + value
        usage.units["acu"] = acu_total if acu_total is not None else committed
        if credits_total is not None:
            usage.units["credits"] = credits_total
        return usage
    except (sqlite3.Error, IndexError, KeyError):
        return None  # banco travado ou formato diferente: vale a estimativa
    finally:
        conn.close()


def find_usage(workdir: str, started: float, ended: float, env: dict[str, str] | None = None) -> DevinUsage | None:
    for db in candidate_dbs(env=env):
        found = read_session_usage(db, workdir, started, ended)
        if found is not None:
            return found
    return None

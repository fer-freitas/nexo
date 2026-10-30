"""SQLite local com migrações versionadas."""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS: tuple[str, ...] = (
    # 1 — log de auditoria encadeado
    """
    CREATE TABLE events (
        seq INTEGER PRIMARY KEY,
        at TEXT NOT NULL,
        actor TEXT NOT NULL,
        type TEXT NOT NULL,
        ref TEXT,
        payload TEXT NOT NULL,
        prev_hash TEXT NOT NULL,
        hash TEXT NOT NULL UNIQUE
    );
    CREATE INDEX idx_events_ref ON events(ref);
    CREATE INDEX idx_events_type ON events(type);
    """,
    # 2 — itens de trabalho, execuções, passos, consumo, handoffs, progresso e estado das rotas
    """
    CREATE TABLE work_items (
        id TEXT PRIMARY KEY,
        type TEXT NOT NULL,
        title TEXT NOT NULL,
        state TEXT NOT NULL,
        project TEXT NOT NULL,
        source TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE runs (
        id TEXT PRIMARY KEY,
        target_id TEXT NOT NULL,
        project TEXT NOT NULL,
        dev TEXT NOT NULL,
        profile TEXT NOT NULL,
        state TEXT NOT NULL,
        reason TEXT,
        branch TEXT,
        worktree TEXT,
        base_sha TEXT,
        brain_version TEXT,
        classification TEXT,
        blocked_until REAL,
        started_at TEXT NOT NULL,
        ended_at TEXT
    );
    CREATE INDEX idx_runs_target ON runs(target_id);
    CREATE TABLE steps (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(id),
        seq INTEGER NOT NULL,
        role TEXT NOT NULL,
        tool TEXT NOT NULL,
        model TEXT NOT NULL,
        outcome TEXT,
        error_kind TEXT,
        message TEXT,
        checkpoint_sha TEXT,
        started_at TEXT NOT NULL,
        ended_at TEXT
    );
    CREATE INDEX idx_steps_run ON steps(run_id);
    CREATE TABLE usage (
        step_id TEXT NOT NULL REFERENCES steps(id),
        unit TEXT NOT NULL,
        quantity REAL NOT NULL,
        api_equiv_usd REAL NOT NULL DEFAULT 0,
        estimated INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_usage_step ON usage(step_id);
    CREATE TABLE handoffs (
        run_id TEXT NOT NULL REFERENCES runs(id),
        seq INTEGER NOT NULL,
        from_route TEXT NOT NULL,
        to_route TEXT,
        reason TEXT NOT NULL,
        packet_path TEXT,
        at TEXT NOT NULL
    );
    CREATE TABLE progress (
        run_id TEXT NOT NULL REFERENCES runs(id),
        step_id TEXT,
        at TEXT NOT NULL,
        data TEXT NOT NULL
    );
    CREATE INDEX idx_progress_run ON progress(run_id);
    CREATE TABLE route_state (
        tool TEXT PRIMARY KEY,
        limited_until REAL,
        utilization REAL,
        reason TEXT,
        updated_at TEXT NOT NULL
    );
    """,
    # 3 — itens e execuções escopados pelo projeto (ids como F-1/T-1 se repetem entre projetos)
    """
    CREATE TABLE work_items_v3 (
        project TEXT NOT NULL,
        id TEXT NOT NULL,
        type TEXT NOT NULL,
        title TEXT NOT NULL,
        state TEXT NOT NULL,
        source TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (project, id)
    );
    INSERT INTO work_items_v3 SELECT project, id, type, title, state, source, updated_at FROM work_items;
    DROP TABLE work_items;
    ALTER TABLE work_items_v3 RENAME TO work_items;
    CREATE INDEX idx_runs_project_target ON runs(project, target_id);
    """,
    # 4 — vínculos com a ferramenta de gestão e fila de sincronização
    """
    CREATE TABLE external_refs (
        project TEXT NOT NULL,
        local_id TEXT NOT NULL,
        tracker TEXT NOT NULL,
        ref TEXT NOT NULL,
        url TEXT NOT NULL,
        parent_local TEXT,
        synced_state TEXT,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (project, local_id, tracker)
    );
    CREATE TABLE pending_sync (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project TEXT NOT NULL,
        local_id TEXT NOT NULL,
        tracker TEXT NOT NULL,
        action TEXT NOT NULL,
        payload TEXT NOT NULL,
        error TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    """,
    # 5 — FinOps: orçamentos, alertas e rateios fechados
    """
    CREATE TABLE budgets (
        dev TEXT NOT NULL,
        month TEXT NOT NULL,
        tool TEXT NOT NULL DEFAULT '',
        limit_brl REAL NOT NULL,
        set_by TEXT NOT NULL,
        set_at TEXT NOT NULL,
        PRIMARY KEY (dev, month, tool)
    );
    CREATE TABLE budget_alerts (
        dev TEXT NOT NULL,
        month TEXT NOT NULL,
        level REAL NOT NULL,
        at TEXT NOT NULL,
        PRIMARY KEY (dev, month, level)
    );
    CREATE TABLE allocations (
        month TEXT NOT NULL,
        step_id TEXT NOT NULL,
        dev TEXT NOT NULL,
        tool TEXT NOT NULL,
        target TEXT NOT NULL,
        project TEXT NOT NULL,
        fixed_brl REAL NOT NULL,
        variable_brl REAL NOT NULL,
        PRIMARY KEY (month, step_id)
    );
    CREATE TABLE months_closed (
        month TEXT PRIMARY KEY,
        closed_by TEXT NOT NULL,
        closed_at TEXT NOT NULL
    );
    """,
    # 6 — aprendizado e avaliação
    """
    CREATE TABLE learning_events (
        project TEXT NOT NULL,
        ref TEXT NOT NULL,
        source TEXT NOT NULL,
        text TEXT NOT NULL,
        meta TEXT NOT NULL,
        collected_at TEXT NOT NULL,
        processed_at TEXT,
        PRIMARY KEY (project, ref)
    );
    CREATE TABLE lessons (
        project TEXT NOT NULL,
        title TEXT NOT NULL,
        type TEXT NOT NULL,
        body TEXT NOT NULL,
        evidence TEXT NOT NULL,
        issue TEXT,
        created_at TEXT NOT NULL
    );
    CREATE TABLE eval_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project TEXT NOT NULL,
        batch TEXT NOT NULL,
        task TEXT NOT NULL,
        kind TEXT NOT NULL,
        variant TEXT NOT NULL,
        route TEXT NOT NULL,
        success INTEGER NOT NULL,
        cost_brl REAL NOT NULL,
        seconds REAL NOT NULL,
        detail TEXT,
        at TEXT NOT NULL
    );
    """,
    # 7 — aprovações externas: solicitações escaladas e créditos de orçamento concedidos por elas
    """
    CREATE TABLE approval_requests (
        id TEXT PRIMARY KEY,
        project TEXT NOT NULL,
        kind TEXT NOT NULL,
        target TEXT NOT NULL,
        dev TEXT NOT NULL,
        month TEXT NOT NULL,
        channel TEXT NOT NULL,
        status TEXT NOT NULL,
        detail TEXT NOT NULL,
        external_ref TEXT,
        created_at TEXT NOT NULL,
        resolved_at TEXT,
        resolved_by TEXT
    );
    CREATE INDEX approval_pending ON approval_requests(status);
    CREATE TABLE budget_grants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dev TEXT NOT NULL,
        month TEXT NOT NULL,
        amount_brl REAL NOT NULL,
        request_id TEXT NOT NULL,
        at TEXT NOT NULL
    );
    """,
    # 8 — outbox da telemetria: gravada na mesma transação do evento; enviada em segundo plano
    """
    CREATE TABLE outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        kind TEXT NOT NULL,
        seq INTEGER NOT NULL DEFAULT 0,
        type TEXT NOT NULL,
        at TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at REAL NOT NULL,
        sent_at REAL,
        status TEXT NOT NULL DEFAULT 'pending',
        attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at REAL NOT NULL DEFAULT 0,
        last_error TEXT
    );
    CREATE INDEX outbox_pending ON outbox(status, id);
    """,
    # 9 — contexto do registro na outbox: projeto/squad/stack de quando o evento ocorreu, não do envio
    """
    ALTER TABLE outbox ADD COLUMN context TEXT NOT NULL DEFAULT '{}';
    """,
    # 10 — chamadas de ferramenta por passo
    """
    ALTER TABLE steps ADD COLUMN tool_calls INTEGER;
    """,
    # 11 — consumo de IA registrado pelas ferramentas, dentro e fora do Nexo, por dia × ferramenta × modelo
    """
    CREATE TABLE external_usage (
        tool TEXT NOT NULL, day TEXT NOT NULL, model TEXT NOT NULL, nexo INTEGER NOT NULL,
        sessions INTEGER NOT NULL, responses INTEGER NOT NULL, units TEXT NOT NULL,
        api_equiv_usd REAL NOT NULL, usage_brl REAL NOT NULL, scanned_at REAL NOT NULL,
        PRIMARY KEY (tool, day, model, nexo)
    );
    """,
)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    return conn


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> int:
    current = schema_version(conn)
    for version, script in enumerate(MIGRATIONS, start=1):
        if version <= current:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            _run_script(conn, script)
            conn.execute(f"PRAGMA user_version={version}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return schema_version(conn)


def _run_script(conn: sqlite3.Connection, script: str) -> None:
    for statement in (s.strip() for s in script.split(";")):
        if statement:
            conn.execute(statement)

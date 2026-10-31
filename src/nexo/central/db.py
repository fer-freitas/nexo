"""Banco do coletor: SQLite (desenvolvimento/testes) ou PostgreSQL (produção), SQL portável e migrações versionadas.

`NX_COLLECTOR_DB` = caminho do SQLite ou URL `postgresql://…`.
`NX_COLLECTOR_BI_ROLE` = papel somente leitura do BI (padrão `nexo_bi`, ver `deploy/collector/bi-readonly.sql`).
"""

from __future__ import annotations

import os
import re
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

MIGRATIONS: tuple[str, ...] = (
    """
    CREATE TABLE devices (device_id TEXT PRIMARY KEY, dev_pid TEXT, status TEXT NOT NULL, created_at DOUBLE PRECISION,
        revoked_at DOUBLE PRECISION);
    CREATE TABLE device_codes (device_code TEXT PRIMARY KEY, user_code TEXT UNIQUE, device_id TEXT NOT NULL,
        status TEXT NOT NULL, dev TEXT, expires_at DOUBLE PRECISION NOT NULL);
    CREATE TABLE tokens (token_hash TEXT PRIMARY KEY, device_id TEXT NOT NULL, kind TEXT NOT NULL,
        expires_at DOUBLE PRECISION NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE identities (dev_pid TEXT PRIMARY KEY, dev TEXT NOT NULL);
    CREATE TABLE raw_events (event_id TEXT PRIMARY KEY, device_id TEXT, dev_pid TEXT, project TEXT, kind TEXT,
        seq INTEGER, type TEXT, at TEXT, payload TEXT, received_at DOUBLE PRECISION);
    CREATE INDEX raw_events_received ON raw_events(received_at);
    CREATE TABLE fact_step (event_id TEXT PRIMARY KEY, dev_pid TEXT, project TEXT, month TEXT, step TEXT, run TEXT,
        target TEXT, story TEXT, role TEXT, tool TEXT, model TEXT, started_at TEXT, seconds DOUBLE PRECISION,
        outcome TEXT, error_kind TEXT, tokens_in DOUBLE PRECISION, tokens_out DOUBLE PRECISION,
        cache_read DOUBLE PRECISION, cache_write DOUBLE PRECISION, premium_request DOUBLE PRECISION,
        acu DOUBLE PRECISION, api_equiv_usd DOUBLE PRECISION, usage_brl DOUBLE PRECISION, estimated INTEGER,
        brain_version TEXT);
    CREATE TABLE anchors (device_id TEXT NOT NULL, seq INTEGER NOT NULL, hash TEXT NOT NULL, at TEXT,
        PRIMARY KEY (device_id, seq));
    CREATE TABLE budgets (dev TEXT NOT NULL, month TEXT NOT NULL, tool TEXT NOT NULL, limit_brl DOUBLE PRECISION,
        set_by TEXT, PRIMARY KEY (dev, month, tool));
    CREATE TABLE invoices (tool TEXT NOT NULL, month TEXT NOT NULL, amount_brl DOUBLE PRECISION,
        measured_brl DOUBLE PRECISION, diff_pct DOUBLE PRECISION, at DOUBLE PRECISION, PRIMARY KEY (tool, month));
    CREATE TABLE audit (id TEXT PRIMARY KEY, at DOUBLE PRECISION, actor TEXT, action TEXT, detail TEXT);
    """,
    # 2 — modelo de fatos, diretório da org, insights, sessões do dashboard, jobs e views para BI
    """
    ALTER TABLE raw_events ADD COLUMN squad TEXT;
    ALTER TABLE raw_events ADD COLUMN stack TEXT;
    ALTER TABLE fact_step ADD COLUMN squad TEXT;
    ALTER TABLE fact_step ADD COLUMN stack TEXT;
    CREATE INDEX fact_step_month ON fact_step(month, squad);
    CREATE INDEX fact_step_dev ON fact_step(dev_pid, month);
    CREATE INDEX raw_events_order ON raw_events(at, event_id);
    CREATE TABLE fact_story (project TEXT NOT NULL, story TEXT NOT NULL, event_id TEXT NOT NULL, at TEXT NOT NULL,
        squad TEXT, stack TEXT, dev_pid TEXT, feature TEXT, points DOUBLE PRECISION, state TEXT, human_gate INTEGER,
        escalated INTEGER, bugs INTEGER, attempts INTEGER, spec_returns INTEGER, clarifications INTEGER,
        handoffs INTEGER, lead_hours DOUBLE PRECISION, cycle_hours DOUBLE PRECISION,
        waiting_human_hours DOUBLE PRECISION, cost_brl DOUBLE PRECISION, cap_brl DOUBLE PRECISION,
        estimate_brl DOUBLE PRECISION, started_at TEXT, done_at TEXT, month TEXT, PRIMARY KEY (project, story));
    CREATE INDEX fact_story_month ON fact_story(month, squad);
    CREATE TABLE fact_gate (event_id TEXT PRIMARY KEY, at TEXT, month TEXT, project TEXT, squad TEXT, stack TEXT,
        dev_pid TEXT, story TEXT, feature TEXT, gate TEXT, decision TEXT, score DOUBLE PRECISION,
        threshold DOUBLE PRECISION, kind TEXT);
    CREATE TABLE fact_quality (event_id TEXT PRIMARY KEY, at TEXT, month TEXT, project TEXT, squad TEXT, stack TEXT,
        dev_pid TEXT, story TEXT, failed INTEGER, coverage DOUBLE PRECISION, findings_critical INTEGER,
        findings_high INTEGER, findings_medium INTEGER, findings_low INTEGER);
    CREATE TABLE fact_event (event_id TEXT PRIMARY KEY, at TEXT, month TEXT, type TEXT, project TEXT, squad TEXT,
        stack TEXT, dev_pid TEXT, story TEXT, task TEXT, feature TEXT, bug TEXT, tool TEXT, kind TEXT, source TEXT,
        rule TEXT, points DOUBLE PRECISION, brl DOUBLE PRECISION);
    CREATE INDEX fact_event_type ON fact_event(type, month);
    CREATE TABLE fact_task (project TEXT NOT NULL, task TEXT NOT NULL, event_id TEXT NOT NULL, at TEXT NOT NULL,
        kind TEXT, size TEXT, PRIMARY KEY (project, task));
    CREATE TABLE dim_directorate (directorate TEXT PRIMARY KEY, name TEXT);
    CREATE TABLE dim_squad (squad TEXT PRIMARY KEY, name TEXT, directorate TEXT);
    CREATE TABLE org_people (dev TEXT PRIMARY KEY, role TEXT NOT NULL);
    CREATE TABLE org_members (dev TEXT NOT NULL, squad TEXT NOT NULL, relation TEXT NOT NULL,
        PRIMARY KEY (dev, squad, relation));
    CREATE TABLE licenses (dev TEXT NOT NULL, tool TEXT NOT NULL, monthly_brl DOUBLE PRECISION,
        acu_included DOUBLE PRECISION, PRIMARY KEY (dev, tool));
    CREATE TABLE tracker_totals (squad TEXT NOT NULL, month TEXT NOT NULL, stories INTEGER,
        PRIMARY KEY (squad, month));
    CREATE TABLE device_lag (device_id TEXT PRIMARY KEY, dev_pid TEXT, lag_hours DOUBLE PRECISION,
        max_lag_hours DOUBLE PRECISION, last_seen DOUBLE PRECISION);
    CREATE TABLE insights (id TEXT PRIMARY KEY, key TEXT UNIQUE NOT NULL, detector TEXT NOT NULL, title TEXT,
        scope_kind TEXT, scope TEXT, squad TEXT, dev_pid TEXT, purpose TEXT, severity TEXT, owner TEXT, action TEXT,
        evidence TEXT, state TEXT NOT NULL, reason TEXT, systemic INTEGER, period TEXT,
        first_seen DOUBLE PRECISION, last_seen DOUBLE PRECISION, updated_at DOUBLE PRECISION, updated_by TEXT,
        issue_url TEXT);
    CREATE INDEX insights_state ON insights(state, detector);
    CREATE TABLE insight_history (id TEXT PRIMARY KEY, insight TEXT NOT NULL, at DOUBLE PRECISION, actor TEXT,
        from_state TEXT, to_state TEXT, reason TEXT);
    CREATE TABLE sessions (sid_hash TEXT PRIMARY KEY, dev TEXT NOT NULL, role TEXT, csrf TEXT NOT NULL,
        expires_at DOUBLE PRECISION NOT NULL, created_at DOUBLE PRECISION);
    CREATE TABLE oidc_states (state TEXT PRIMARY KEY, verifier TEXT NOT NULL, next TEXT,
        expires_at DOUBLE PRECISION NOT NULL);
    CREATE TABLE jobs (job TEXT NOT NULL, period TEXT NOT NULL, at DOUBLE PRECISION, status TEXT, detail TEXT,
        PRIMARY KEY (job, period));
    CREATE TABLE deliveries (id TEXT PRIMARY KEY, at DOUBLE PRECISION, channel TEXT, kind TEXT, target TEXT,
        status TEXT, payload TEXT);
    CREATE VIEW bi_fact_step AS SELECT event_id, project, squad, stack, month, step, run, target, story, role, tool,
        model, started_at, seconds, outcome, error_kind, tokens_in, tokens_out, cache_read, cache_write,
        premium_request, acu, api_equiv_usd, usage_brl, estimated, brain_version FROM fact_step;
    CREATE VIEW bi_fact_story AS SELECT project, story, squad, stack, feature, points, state, human_gate, escalated,
        bugs, attempts, spec_returns, clarifications, handoffs, lead_hours, cycle_hours, waiting_human_hours,
        cost_brl, cap_brl, estimate_brl, started_at, done_at, month FROM fact_story;
    CREATE VIEW bi_fact_gate AS SELECT event_id, at, month, project, squad, stack, story, feature, gate, decision,
        score, threshold, kind FROM fact_gate;
    CREATE VIEW bi_fact_quality AS SELECT event_id, at, month, project, squad, stack, story, failed, coverage,
        findings_critical, findings_high, findings_medium, findings_low FROM fact_quality;
    CREATE VIEW bi_dim_squad AS SELECT s.squad, s.name, s.directorate, d.name AS directorate_name FROM dim_squad s
        LEFT JOIN dim_directorate d ON d.directorate = s.directorate;
    """,
    # 3 — kill switch central e linha de base de métricas por squad
    """
    CREATE TABLE kill_switch (id TEXT PRIMARY KEY, data TEXT NOT NULL, updated_at DOUBLE PRECISION, updated_by TEXT);
    CREATE TABLE baselines (squad TEXT NOT NULL, project TEXT NOT NULL, period TEXT NOT NULL, data TEXT NOT NULL,
        at DOUBLE PRECISION, PRIMARY KEY (squad, project));
    """,
    # 4 — frota, alertas de custo em tempo real, SIEM, DORA/incidentes
    """
    ALTER TABLE fact_step ADD COLUMN tool_calls INTEGER;
    CREATE TABLE fleet (device_id TEXT PRIMARY KEY, dev_pid TEXT, squad TEXT, nexo TEXT, brain TEXT, agents TEXT,
        os TEXT, last_seen DOUBLE PRECISION);
    CREATE TABLE siem_outbox (id TEXT PRIMARY KEY, at DOUBLE PRECISION, event TEXT NOT NULL, status TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT);
    CREATE TABLE deployments (id TEXT PRIMARY KEY, service TEXT NOT NULL, env TEXT NOT NULL, sha TEXT,
        status TEXT NOT NULL, started_at TEXT, finished_at TEXT NOT NULL, squad TEXT, commits TEXT,
        nexo_commits INTEGER, lead_time_hours DOUBLE PRECISION, month TEXT);
    CREATE INDEX deployments_month ON deployments(month, service);
    CREATE TABLE incidents (id TEXT PRIMARY KEY, service TEXT NOT NULL, opened_at TEXT NOT NULL, resolved_at TEXT,
        severity TEXT, cause_sha TEXT, cause_deployment TEXT, nexo_caused INTEGER, squad TEXT, month TEXT);
    """,
    # 5 — autonomia por squad × área e esclarecimentos respondidos pelo Teams
    """
    CREATE TABLE autonomy (squad TEXT NOT NULL, area TEXT NOT NULL, level TEXT NOT NULL,
        updated_at DOUBLE PRECISION, updated_by TEXT, reason TEXT, PRIMARY KEY (squad, area));
    CREATE TABLE autonomy_history (id TEXT PRIMARY KEY, squad TEXT, area TEXT, from_level TEXT, to_level TEXT,
        at DOUBLE PRECISION, actor TEXT, reason TEXT, kind TEXT);
    CREATE TABLE clarifications (id TEXT PRIMARY KEY, dev_pid TEXT NOT NULL, device_id TEXT, created_at
        DOUBLE PRECISION, answer TEXT, answered_by TEXT, answered_at DOUBLE PRECISION, delivered INTEGER DEFAULT 0);
    """,
    # 6 — feature flags (global = squad vazio) e histórico
    """
    CREATE TABLE flags (key TEXT NOT NULL, squad TEXT NOT NULL, value INTEGER NOT NULL, updated_at DOUBLE PRECISION,
        updated_by TEXT, reason TEXT, PRIMARY KEY (key, squad));
    CREATE TABLE flag_history (id TEXT PRIMARY KEY, key TEXT, squad TEXT, from_value INTEGER, to_value INTEGER,
        at DOUBLE PRECISION, actor TEXT, reason TEXT);
    """,
    # 7 — Copilot cobrado em AI Credits (consumo real do session-store.db)
    """
    ALTER TABLE fact_step ADD COLUMN ai_credits DOUBLE PRECISION;
    DROP VIEW bi_fact_step;
    CREATE VIEW bi_fact_step AS SELECT event_id, project, squad, stack, month, step, run, target, story, role, tool,
        model, started_at, seconds, outcome, error_kind, tokens_in, tokens_out, cache_read, cache_write,
        premium_request, acu, ai_credits, api_equiv_usd, usage_brl, estimated, brain_version FROM fact_step;
    """,
    # 8 — IA usada fora do Nexo, total do dia por dispositivo × ferramenta × modelo (o retrato mais novo vale)
    """
    CREATE TABLE fact_external_usage (device_id TEXT NOT NULL, dev_pid TEXT, squad TEXT, month TEXT NOT NULL,
        day TEXT NOT NULL, tool TEXT NOT NULL, model TEXT NOT NULL, sessions INTEGER, responses INTEGER,
        tokens_in DOUBLE PRECISION, tokens_out DOUBLE PRECISION, cache_read DOUBLE PRECISION,
        cache_write DOUBLE PRECISION, premium_request DOUBLE PRECISION, ai_credits DOUBLE PRECISION,
        acu DOUBLE PRECISION, api_equiv_usd DOUBLE PRECISION, usage_brl DOUBLE PRECISION, at TEXT NOT NULL,
        PRIMARY KEY (device_id, tool, model, day));
    CREATE INDEX fact_external_month ON fact_external_usage(month, squad);
    """,
    # 9 — aprovação pelo Teams (só o id, o portão e os pseudônimos; motivo e diff ficam na máquina do dev)
    """
    CREATE TABLE remote_approvals (id TEXT PRIMARY KEY, dev_pid TEXT NOT NULL, device_id TEXT, gate TEXT,
        created_at DOUBLE PRECISION, decision TEXT, reason TEXT, decided_by TEXT, decided_pid TEXT,
        decided_at DOUBLE PRECISION);
    """,
    # 10 — centro de custo do squad (ou da diretoria, quando o squad não tem) para o chargeback
    """
    ALTER TABLE dim_directorate ADD COLUMN cost_center TEXT;
    ALTER TABLE dim_squad ADD COLUMN cost_center TEXT;
    """,
    # 11 — aderência ao padrão de código (arquivos verificados, sem violação, violações e erros) e por regra
    """
    ALTER TABLE fact_quality ADD COLUMN standards_files INTEGER;
    ALTER TABLE fact_quality ADD COLUMN standards_ok INTEGER;
    ALTER TABLE fact_quality ADD COLUMN standards_violations INTEGER;
    ALTER TABLE fact_quality ADD COLUMN standards_errors INTEGER;
    CREATE TABLE fact_standards_rule (event_id TEXT NOT NULL, rule TEXT NOT NULL, at TEXT, month TEXT, project TEXT,
        squad TEXT, stack TEXT, count INTEGER, PRIMARY KEY (event_id, rule));
    CREATE INDEX fact_standards_month ON fact_standards_rule(month, squad);
    DROP VIEW bi_fact_quality;
    CREATE VIEW bi_fact_quality AS SELECT event_id, at, month, project, squad, stack, story, failed, coverage,
        findings_critical, findings_high, findings_medium, findings_low, standards_files, standards_ok,
        standards_violations, standards_errors FROM fact_quality;
    CREATE VIEW bi_fact_standards AS SELECT event_id, month, project, squad, stack, rule, count
        FROM fact_standards_rule;
    """,
    """
    CREATE TABLE dim_project (project TEXT PRIMARY KEY, name TEXT NOT NULL, squad TEXT, updated_at DOUBLE PRECISION);
    """,
    """
    CREATE TABLE fact_demand (
        project TEXT NOT NULL, demand TEXT NOT NULL, squad TEXT NOT NULL DEFAULT '', demand_type TEXT, stage TEXT,
        origin TEXT, title TEXT, parent TEXT, holders TEXT, blocked INTEGER DEFAULT 0, updated_at TEXT,
        PRIMARY KEY (project, demand)
    );
    CREATE TABLE portal_invites (
        token_hash TEXT PRIMARY KEY, dev TEXT NOT NULL, role TEXT NOT NULL, expires_at REAL NOT NULL,
        used_at REAL, created_by TEXT NOT NULL, created_at REAL NOT NULL
    );
    CREATE TABLE portal_login_attempts (key TEXT PRIMARY KEY, attempts INTEGER NOT NULL, until REAL NOT NULL);
    CREATE TABLE insight_settings (detector TEXT PRIMARY KEY, enabled INTEGER NOT NULL, updated_by TEXT, updated_at REAL);
    """,
    # sigla do projeto na empresa: vários repositórios (hashes) dividem a mesma
    """
    ALTER TABLE dim_project ADD COLUMN sigla TEXT;
    CREATE INDEX dim_project_sigla ON dim_project(sigla);
    """,
)

BI_VIEWS = ("bi_fact_step", "bi_fact_story", "bi_fact_gate", "bi_fact_quality", "bi_fact_standards", "bi_dim_squad")


class CollectorDB:
    def __init__(self, url: str | None = None) -> None:
        self.url = url or os.environ.get("NX_COLLECTOR_DB", "nexo-collector.db")
        self.pg = self.url.startswith(("postgresql://", "postgres://"))
        self._lock = threading.RLock()  # jobs em segundo plano dividem a conexão
        self._conn: Any = self._connect()
        self.migrate()

    def _connect(self) -> Any:
        if self.pg:
            import psycopg  # extra `collector`
            from psycopg.rows import dict_row

            return psycopg.connect(self.url, autocommit=True, row_factory=dict_row)
        conn = sqlite3.connect(self.url, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _sql(self, sql: str) -> str:
        """SQL portável: `?` vira `%s` no PostgreSQL; `%` literal (ex.: LIKE) é escapado."""
        return sql.replace("%", "%%").replace("?", "%s") if self.pg else sql

    @contextmanager
    def tx(self) -> Iterator[CollectorDB]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        with self._lock:
            cur = self._conn.execute(self._sql(sql), params)
            return int(cur.rowcount or 0)

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(self._sql(sql), params)
            return [dict(r) for r in cur.fetchall()]

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def migrate(self) -> int:
        with self._lock:
            self._conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
            row = self._conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = int((dict(row) if row else {}).get("v") or 0)
            for version, script in enumerate(MIGRATIONS, start=1):
                if version <= current:
                    continue
                for statement in [s.strip() for s in script.split(";") if s.strip()]:
                    self._conn.execute(statement)
                self._conn.execute(self._sql("INSERT INTO schema_version (version) VALUES (?)"), (version,))
            if self.pg and current < len(MIGRATIONS):
                self.grant_bi()
            return len(MIGRATIONS)

    def grant_bi(self) -> bool:
        """Devolve ao papel de BI a leitura das views `bi_*`: no PostgreSQL, recriar uma view (ex.: migração 7)
        apaga a permissão. Só age se o papel já existir; criar o papel continua sendo do administrador."""
        role = os.environ.get("NX_COLLECTOR_BI_ROLE", "nexo_bi")
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
            raise ValueError(f"nome de papel de BI inválido: {role}")
        with self._lock:
            found = self._conn.execute(self._sql("SELECT 1 FROM pg_roles WHERE rolname = ?"), (role,)).fetchone()
            if not found:
                return False
            # nome conferido pela expressão acima (identificador simples), não vem do usuário final
            self._conn.execute(f"GRANT USAGE ON SCHEMA public TO {role}")  # nosemgrep -- papel validado acima
            self._conn.execute(f"GRANT SELECT ON {', '.join(BI_VIEWS)} TO {role}")  # nosemgrep -- papel validado acima
            return True

    def close(self) -> None:
        self._conn.close()

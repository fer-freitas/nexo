"""C-07: no PostgreSQL, a migração devolve ao papel de BI a leitura das views recriadas."""

from __future__ import annotations

from typing import Any

import pytest

from nexo.central.db import BI_VIEWS, MIGRATIONS, CollectorDB


class FakeCursor:
    def __init__(self, row: Any = None) -> None:
        self.row = row

    def fetchone(self) -> Any:
        return self.row


class FakePg:
    def __init__(self, version: int, role_exists: bool) -> None:
        self.version, self.role_exists, self.sql = version, role_exists, []  # type: list[str]

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> FakeCursor:
        self.sql.append(sql)
        if "MAX(version)" in sql:
            return FakeCursor({"v": self.version})
        if "pg_roles" in sql:
            return FakeCursor((1,) if self.role_exists else None)
        return FakeCursor()


def db_with(conn: FakePg) -> CollectorDB:
    db = CollectorDB.__new__(CollectorDB)
    db.url, db.pg, db._conn = "postgresql://x", True, conn
    import threading

    db._lock = threading.RLock()
    return db


def test_grants_restored_after_migration() -> None:
    conn = FakePg(version=6, role_exists=True)
    db_with(conn).migrate()
    assert any(s.startswith("DROP VIEW bi_fact_step") for s in conn.sql)
    assert "GRANT USAGE ON SCHEMA public TO nexo_bi" in conn.sql
    assert f"GRANT SELECT ON {', '.join(BI_VIEWS)} TO nexo_bi" in conn.sql


def test_no_grant_when_nothing_migrated_or_role_missing() -> None:
    up_to_date = FakePg(version=len(MIGRATIONS), role_exists=True)
    db_with(up_to_date).migrate()
    assert not any("GRANT" in s for s in up_to_date.sql)
    no_role = FakePg(version=6, role_exists=False)
    db_with(no_role).migrate()
    assert not any("GRANT" in s for s in no_role.sql)


def test_custom_role_and_invalid_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NX_COLLECTOR_BI_ROLE", "bi_leitura")
    conn = FakePg(version=len(MIGRATIONS), role_exists=True)
    assert db_with(conn).grant_bi() and "GRANT USAGE ON SCHEMA public TO bi_leitura" in conn.sql
    monkeypatch.setenv("NX_COLLECTOR_BI_ROLE", "x; DROP TABLE fact_step")
    with pytest.raises(ValueError):
        db_with(FakePg(0, True)).grant_bi()

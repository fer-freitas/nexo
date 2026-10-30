from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import Runner, edit


def test_removed_variable_names_are_audited_without_values(
    project: Path,
    nexo: Runner,
    scenario,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    monkeypatch.setenv("CORP_DB_PASSWORD", "valor-que-nao-pode-vazar")
    scenario([{"tool": "claude", "events": [edit(), {"progress": {"percent": 100}}]}])
    assert nexo("run", "task", "T-1")[0] == 0
    events = json.loads(nexo("--json", "logs", "-n", "200")[1])
    filtered = [e for e in events if e["type"] == "agent.env_filtered"]
    assert filtered and "CORP_DB_PASSWORD" in filtered[0]["payload"]["removed"]
    assert "valor-que-nao-pode-vazar" not in json.dumps(events)

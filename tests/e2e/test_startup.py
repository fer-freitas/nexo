from __future__ import annotations

import os
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import pytest

from nexo import __version__
from tests.conftest import ROOT

SNAPSHOTS = ROOT / "tests" / "snapshots"


def run_nexo(args: list[str], home: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "NX_HOME": str(home),
        "COLUMNS": "200",
        "NO_COLOR": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    return subprocess.run(
        [sys.executable, "-m", "nexo", *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        encoding="utf-8",
    )


def test_banner_under_one_second(tmp_path: Path) -> None:
    run_nexo([], tmp_path)  # aquece cache de bytecode
    timings = []
    for _ in range(3):
        start = time.perf_counter()
        proc = run_nexo([], tmp_path)
        timings.append(time.perf_counter() - start)
        assert proc.returncode == 0
    assert min(timings) < 1.0, timings


@pytest.mark.parametrize("lang", ["pt-BR", "en"])
def test_help_snapshot(tmp_path: Path, lang: str) -> None:
    proc = run_nexo(["--lang", lang, "help"], tmp_path)
    assert proc.returncode == 0
    snapshot = SNAPSHOTS / f"help.{lang}.txt"
    if os.environ.get("NX_UPDATE_SNAPSHOTS") == "1" or not snapshot.exists():
        snapshot.write_text(proc.stdout, encoding="utf-8")
    assert proc.stdout == snapshot.read_text(encoding="utf-8")


def test_version_matches_pyproject() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["version"] == __version__

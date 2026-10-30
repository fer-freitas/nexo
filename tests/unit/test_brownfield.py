from __future__ import annotations

from pathlib import Path

from nexo.kernel import brownfield
from nexo.kernel.brownfield import FileInfo
from nexo.kernel.workspace import codemap
from nexo.modules.quality.adapters.runner import _adapt


def test_fingerprint_ignores_line_numbers_and_separators() -> None:
    a = brownfield.fingerprint("lint", "F401", "src/a.py", "`os` imported but unused (line 3)")
    b = brownfield.fingerprint("lint", "F401", "src\\a.py", "`os` imported but unused (line 9)")
    assert a == b and a != brownfield.fingerprint("lint", "F401", "src/b.py", "`os` imported but unused")


def test_diff_coverage_counts_only_executable_changed_lines() -> None:
    covered = {"billing/invoice.py": {10: 1, 11: 0, 12: 3}}
    value, hit, total = brownfield.diff_coverage({"src/billing/invoice.py": {10, 11, 12, 40}}, covered)
    assert (value, hit, total) == (66.7, 2, 3)
    assert brownfield.diff_coverage({"README.md": {1}}, covered) == (None, 0, 0)


def test_hotspots_conventions_and_context_without_author_names() -> None:
    files = [
        FileInfo("src/a.py", "python", 50, ["def calcTotal(itens)", "def formatMoney(v)"], [], 9, 4, "@ana"),
        FileInfo("src/b.py", "python", 20, ["def gerarResumo(p)"], ["billing.a"], 1, 1, "Fulano de Tal"),
        FileInfo("tests/test_a.py", "python", 5, ["def test_x()"], ["a"], 0, 9, "", is_test=True),
    ]
    assert [f.path for f in brownfield.hotspots(files)] == ["src/a.py", "src/b.py"]
    conv = brownfield.conventions(files, {"ruff": True, "black": False}, ["pytest"])
    assert conv["naming"] == {"python": "camelCase"} and conv["tools"] == ["ruff"] and conv["test_dir"] == "tests"
    related = brownfield.related(files, ["src/a.py"])
    assert {f.path for f in related} == {"src/a.py", "src/b.py"}
    text = brownfield.context_text(related, 10_000)
    assert "def calcTotal(itens)" in text and "Fulano" not in text and "@ana" not in text
    assert "omitido(s) pelo orçamento" in brownfield.context_text(related, 60)


def test_project_commands_and_override(tmp_path: Path) -> None:
    (tmp_path / "Makefile").write_text("lint:\n\truff check src\ntest:\n\tpytest -q\n", encoding="utf-8")
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("steps:\n  - run: dotnet build\n", encoding="utf-8")
    found = codemap.pipeline_commands(tmp_path)
    assert found["lint"][0] == {"command": "ruff check src", "source": "Makefile"}
    assert found["unit"][0]["command"] == "pytest -q" and found["build"][0]["source"] == ".github/workflows/ci.yml"
    stage = {"id": "lint", "run": ["x", "."], "parser": "exitcode", "tool": "uv", "changed_only": [".py"]}
    adapted = _adapt(stage, {"src/a.py": {1}, "README.md": {1}}, {"lint": "ruff check --select F"})
    assert adapted["run"] == ["ruff", "check", "--select", "F", "src/a.py"] and "tool" not in adapted
    assert _adapt(stage, {"README.md": {1}}, None)["_skip"]


def test_sarif_paths_become_relative_on_windows_and_posix(tmp_path: Path) -> None:
    from nexo.kernel.quality.model import Finding, StageResult
    from nexo.modules.quality.adapters.runner import _relative_findings, _uri_to_path

    assert _uri_to_path("file:///D:/a/nexo/src/x%20y.py") == "D:/a/nexo/src/x y.py"
    assert _uri_to_path("src\\a.py") == "src/a.py"
    root = tmp_path.resolve().as_posix()
    result = StageResult(
        "lint",
        "lint",
        "failed",
        findings=[
            Finding("F401", "m", file=f"file://{'/' if not root.startswith('/') else ''}{root}/src/a.py"),
            Finding("F401", "m", file=f"{root.upper()}/src/b.py" if ":" in root else f"{root}/src/b.py"),
            Finding("F401", "m", file="src/c.py"),
        ],
    )
    _relative_findings(result, tmp_path)
    assert [f.file for f in result.findings] == ["src/a.py", "src/b.py", "src/c.py"]

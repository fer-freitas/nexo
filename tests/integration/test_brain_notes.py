"""Clone de trabalho x cópia verificada: o Obsidian nunca quebra a assinatura, e o lint vale sem CI."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tests.conftest import Runner


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8").stdout


def make_brain(tmp_path: Path) -> Path:
    root = tmp_path / "nexo-brain"
    (root / "lessons").mkdir(parents=True)
    (root / "brain.yaml").write_text("schema_version: 2\n", encoding="utf-8")
    (root / ".gitignore").write_text(".obsidian/\n.trash/\n", encoding="utf-8")
    (root / "lessons" / "boa.md").write_text(
        "---\nid: boa\ntype: lesson\nscope: global\n---\n\nTexto.\n", encoding="utf-8"
    )
    git(root.parent, "init", "-q", str(root))
    git(root, "config", "user.email", "t@t")
    git(root, "config", "user.name", "t")
    git(root, "checkout", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "i")
    return root


def test_sign_refuses_dirty_tree_and_broken_notes_but_ignores_obsidian(tmp_path: Path, nexo: Runner) -> None:
    brain = make_brain(tmp_path)
    key = tmp_path / "chave.pem"
    assert nexo("brain", "keygen", str(key))[0] == 0
    (brain / ".obsidian").mkdir()
    (brain / ".obsidian" / "app.json").write_text("{}", encoding="utf-8")
    code, _, err = nexo("brain", "sign", str(brain), "--key", str(key), "--version", "2026.11.01-1")
    assert code == 0, err
    manifest = json.loads((brain / ".brain" / "manifest.json").read_text(encoding="utf-8"))
    assert ".obsidian/app.json" not in manifest["files"] and "lessons/boa.md" in manifest["files"]
    git(brain, "add", "-A")
    git(brain, "commit", "-qm", "manifesto")
    (brain / "lessons" / "ruim.md").write_text("sem frontmatter", encoding="utf-8")
    code, _, err = nexo("brain", "sign", str(brain), "--key", str(key), "--version", "2026.11.01-2")
    assert code != 0 and "suja" in err
    git(brain, "add", "-A")
    git(brain, "commit", "-qm", "ruim")
    code, _, err = nexo("brain", "sign", str(brain), "--key", str(key), "--version", "2026.11.01-2")
    assert code != 0 and "lint" in err


def test_lint_index_and_propose_by_branch(tmp_path: Path, nexo: Runner) -> None:
    brain = make_brain(tmp_path)
    code, out, _ = nexo("--json", "brain", "lint", str(brain))
    assert code == 0 and json.loads(out)["errors"] == 0
    data = json.loads(nexo("--json", "brain", "index", str(brain))[1])
    assert data["nodes"] == 1
    source = tmp_path / "nova.md"
    source.write_text("---\nid: nova\ntype: lesson\nscope: global\n---\n\nVeja [[boa]].\n", encoding="utf-8")
    code, out, err = nexo(
        "--json", "brain", "propose", str(source), "--clone", str(brain), "--target", "lessons/nova.md"
    )
    assert code == 0, err
    result = json.loads(out)
    assert result["branch"] == "nexo/proposta/nova"
    assert "lessons/nova.md" in git(brain, "ls-files")
    git(brain, "checkout", "-q", "main")
    bad = tmp_path / "ruim.md"
    bad.write_text("---\nid: outra\ntype: lesson\nscope: global\n---\n\nVeja [[nada]].\n", encoding="utf-8")
    code, _, err = nexo("--json", "brain", "propose", str(bad), "--clone", str(brain), "--target", "lessons/outra.md")
    assert code != 0 and "link quebrado" in err
    assert not (brain / "lessons" / "outra.md").exists()


def test_promote_moves_to_global_in_a_branch(tmp_path: Path, nexo: Runner) -> None:
    brain = make_brain(tmp_path)
    folder = brain / "projects" / "loja" / "lessons"
    folder.mkdir(parents=True)
    (folder / "util.md").write_text(
        "---\nid: util\ntype: lesson\nscope: project:loja\n---\n\nTexto.\n", encoding="utf-8"
    )
    git(brain, "add", "-A")
    git(brain, "commit", "-qm", "nota")
    code, out, err = nexo("--json", "brain", "promote", str(brain), "util")
    assert code == 0, err
    assert json.loads(out)["target"] == "lessons/util.md"
    assert "scope: global" in (brain / "lessons" / "util.md").read_text(encoding="utf-8")
    assert not (folder / "util.md").exists()


def test_review_applies_the_same_lint_to_a_pull_request(tmp_path: Path, nexo: Runner) -> None:
    brain = make_brain(tmp_path)
    remote = tmp_path / "remote.git"
    git(tmp_path, "clone", "-q", "--bare", str(brain), str(remote))
    git(brain, "remote", "add", "origin", str(remote))
    git(brain, "checkout", "-q", "-b", "proposta")
    (brain / "lessons" / "ruim.md").write_text(
        "---\nid: ruim\ntype: lesson\nscope: global\n---\n\n[[nada]]\n", encoding="utf-8"
    )
    git(brain, "add", "-A")
    git(brain, "commit", "-qm", "ruim")
    git(brain, "push", "-q", "origin", "proposta:refs/pull/7/head")
    git(brain, "checkout", "-q", "main")
    code, out, _ = nexo("--json", "brain", "review", "7", "--path", str(brain))
    assert code != 0 and json.loads(out)["errors"]
    assert not (brain / "lessons" / "ruim.md").exists(), "a revisão não mexe na pasta de trabalho"
    assert "nexo-review" not in git(brain, "worktree", "list")


def test_graph_traversal_finds_neighbors_and_path(tmp_path: Path, nexo: Runner, nexo_home: Path) -> None:
    from nexo.modules.knowledge.domain.notes import neighbors, path

    edges = [
        {"from": "a", "to": "b", "rel": "x"},
        {"from": "b", "to": "c", "rel": "x"},
        {"from": "z", "to": "y", "rel": "x"},
    ]
    assert neighbors(edges, "a", 1) == {"b": 1}
    assert neighbors(edges, "a", 2) == {"b": 1, "c": 2}
    assert path(edges, "a", "c") == ["a", "b", "c"] and path(edges, "a", "y") == []
    brain = nexo_home / "brain" / "lessons"
    brain.mkdir(parents=True)
    (brain / "um.md").write_text("---\nid: um\ntype: lesson\nscope: global\n---\n\nVer [[dois]].\n", encoding="utf-8")
    (brain / "dois.md").write_text("---\nid: dois\ntype: lesson\nscope: global\n---\n\nTexto.\n", encoding="utf-8")
    code, out, err = nexo("--json", "brain", "neighbors", "um")
    assert code == 0, err + out
    assert [n["id"] for n in json.loads(out)["neighbors"]] == ["dois"]
    assert json.loads(nexo("--json", "brain", "path", "um", "dois")[1])["path"] == ["um", "dois"]


def test_context_follows_note_links_without_crossing_projects(project: Path, nexo: Runner, nexo_home: Path) -> None:
    from ruamel.yaml import YAML

    from nexo.kernel.context import AppContext, Options
    from nexo.modules.knowledge.app import graph

    (project / "src" / "svc" / "version.py").write_text("x = 1\n", encoding="utf-8")
    brain = nexo_home / "brain"
    for rel, text in {
        "knowledge/pagamento.md": "Regra de `src/svc/version.py`. Veja [[retentativa]] e [[segredo]].",
        "knowledge/retentativa.md": "Três tentativas, e veja [[distante]].",
        "knowledge/distante.md": "Dois saltos de distância.",
        "projects/outro/segredo.md": "Só do outro projeto.",
    }.items():
        (brain / rel).parent.mkdir(parents=True, exist_ok=True)
        (brain / rel).write_text(text, encoding="utf-8")

    def context() -> str:
        ctx = AppContext(options=Options())
        try:
            return graph.context_for(ctx, ["src/svc/version.py"])
        finally:
            ctx.close()

    (brain / "org").mkdir(parents=True, exist_ok=True)
    yaml = YAML()
    with (brain / "org" / "flags.yaml").open("w", encoding="utf-8") as fh:
        yaml.dump({"flags": {"brain.graph": False}}, fh)
    assert "knowledge/pagamento.md" in context() and "retentativa" not in context()
    (brain / "org" / "flags.yaml").unlink()
    text = context()
    assert "Notas vizinhas no cérebro: knowledge/retentativa.md" in text
    assert "segredo" not in text and "distante" not in text

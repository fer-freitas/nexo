from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from nexo.kernel.errors import NexoError
from nexo.modules.planning.adapters import ingest
from nexo.modules.planning.app.plan import contract_problems


def test_text_document_image_and_redaction(tmp_path: Path) -> None:
    md = tmp_path / "prd.md"
    md.write_text("Cliente CPF 529.982.247-25 e token ghp_abcdefghijklmnopqrstuvwxyz0123456789ABCD", encoding="utf-8")
    html = tmp_path / "pagina.html"
    html.write_text("<html><body><h1>Título</h1><p>Olá mundo</p></body></html>", encoding="utf-8")
    img = tmp_path / "tela.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    out = ingest.ingest([str(md), str(html), str(img), "texto livre com senha=Segredo123"], tmp_path / "in")
    kinds = [i.kind for i in out]
    assert kinds == ["text", "document", "image", "text"]
    text = Path(out[0].path).read_text(encoding="utf-8")
    assert "529.982" not in text and "ghp_" not in text and out[0].redactions == 2
    assert "Título" in Path(out[1].path).read_text(encoding="utf-8")
    assert Path(out[2].path).exists() and "visão" in out[2].note
    assert Path(out[3].path).name == "texto.md" and "Segredo123" not in Path(out[3].path).read_text(encoding="utf-8")


def test_same_name_does_not_overwrite(tmp_path: Path) -> None:
    out = ingest.ingest(["um", "dois"], tmp_path / "in")
    assert Path(out[0].path).name == "texto.md" and Path(out[1].path).name == "texto-2.md"


def test_audio_with_fake_transcriber(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    audio = tmp_path / "reuniao.m4a"
    audio.write_bytes(b"x")
    monkeypatch.setenv("NX_FAKE_TRANSCRIPT", "[000.0s] precisamos de estorno parcial")
    out = ingest.ingest([str(audio)], tmp_path / "in")
    assert out[0].kind == "audio" and "estorno parcial" in Path(out[0].path).read_text(encoding="utf-8")


def test_audio_without_whisper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    monkeypatch.delenv("NX_FAKE_TRANSCRIPT", raising=False)
    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "faster_whisper":
            raise ImportError
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(NexoError, match="media"):
        ingest.transcribe(tmp_path / "a.wav")


def test_video_requires_ffmpeg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "demo.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(ingest.shutil, "which", lambda _: None)
    with pytest.raises(NexoError, match="ffmpeg"):
        ingest.ingest([str(video)], tmp_path / "in")


def test_video_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = tmp_path / "demo.mp4"
    video.write_bytes(b"x")
    dest = tmp_path / "in"
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], check: bool) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[-1].endswith(".jpg"):
            for i in (1, 2):
                (dest / f"demo-frame-0{i}.jpg").write_bytes(b"j")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(ingest.shutil, "which", lambda _: "/usr/bin/ffmpeg")
    monkeypatch.setattr(ingest.subprocess, "run", fake_run)
    monkeypatch.setenv("NX_FAKE_TRANSCRIPT", "fala do vídeo")
    out = ingest.ingest([str(video)], dest)
    assert [i.kind for i in out] == ["video", "image", "image"] and out[0].note == "2 quadro(s)"
    assert len(calls) == 2


def test_figma(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = {
        "name": "App",
        "document": {
            "type": "DOCUMENT",
            "children": [
                {
                    "type": "CANVAS",
                    "name": "Pagamentos",
                    "children": [
                        {"type": "FRAME", "name": "Tela estorno", "children": [{"type": "TEXT", "characters": "Valor"}]}
                    ],
                }
            ],
        },
    }
    monkeypatch.setattr(ingest, "_http_get_json", lambda url, token: doc)
    url = "https://www.figma.com/design/AbC123/App"
    out = ingest.ingest([url], tmp_path / "in", figma_token="t")
    text = Path(out[0].path).read_text(encoding="utf-8")
    assert "Tela estorno" in text and "- Valor" in text
    with pytest.raises(NexoError, match="login figma"):
        ingest.ingest([url], tmp_path / "in")
    with pytest.raises(NexoError, match="inválido"):
        ingest.figma_text("https://example.com", "t")


def test_unsupported_format(tmp_path: Path) -> None:
    f = tmp_path / "x.exe"
    f.write_bytes(b"x")
    with pytest.raises(NexoError, match="não suportado"):
        ingest.ingest([str(f)], tmp_path / "in")


OPENAPI = """openapi: 3.1.0
info: {title: Refunds, version: 1.0.0}
paths:
  /refunds:
    post:
      responses:
        '201': {description: criado}
"""


def test_contract_validation() -> None:
    assert contract_problems([{"path": "contracts/refunds.yaml", "content": OPENAPI}]) == []
    assert contract_problems([{"path": "contracts/s.json", "content": '{"type": "object"}'}]) == []
    problems = contract_problems(
        [
            {"path": "../x.yaml", "content": OPENAPI},
            {"path": "contracts/bad.yaml", "content": "openapi: 3.1.0\npaths: 3"},
            {"path": "contracts/bad-schema.json", "content": '{"type": 12}'},
            {"path": "contracts/empty.yaml", "content": ""},
            {"path": "contracts/broken.yaml", "content": "a: [1,"},
            {"path": "contracts/events.yaml", "content": "asyncapi: 3.0.0\ninfo: {title: x, version: 1}"},
        ]
    )
    joined = " | ".join(problems)
    for fragment in (
        "fora de contracts/",
        "OpenAPI inválido",
        "JSON Schema inválido",
        "vazio",
        "inválido (",
        "sem channels",
    ):
        assert fragment in joined, fragment

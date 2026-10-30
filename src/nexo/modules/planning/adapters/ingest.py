"""Ingestão multimodal determinística: tudo vira markdown/imagem redigidos em `inputs/`."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexo.kernel.errors import NexoError
from nexo.kernel.security.redaction import find, redact

TEXT_EXT = {".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".feature"}
DOC_EXT = {".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".html", ".htm"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".webm", ".aiff", ".aif"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
FIGMA_URL = re.compile(r"https://(?:www\.)?figma\.com/(?:file|design|proto)/([A-Za-z0-9]+)")
MAX_CHARS = 60_000


@dataclass(frozen=True)
class Ingested:
    kind: str  # text | document | audio | video | image | figma
    source: str
    path: str  # arquivo gerado em inputs/
    chars: int
    redactions: int
    note: str = ""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "entrada"


def _write_text(dest_dir: Path, name: str, text: str) -> tuple[Path, int, int]:
    redactions = len(find(text))
    clean = redact(text)[:MAX_CHARS]
    path = dest_dir / f"{name}.md"
    n = 2
    while path.exists():
        path = dest_dir / f"{name}-{n}.md"
        n += 1
    path.write_text(clean, encoding="utf-8")
    return path, len(clean), redactions


def convert_document(path: Path) -> str:
    from markitdown import MarkItDown  # import tardio: dependência pesada

    return str(MarkItDown().convert(str(path)).text_content)


def transcribe(path: Path, model: str = "base") -> str:
    fake = os.environ.get("NX_FAKE_TRANSCRIPT")
    if fake is not None:
        return fake
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise NexoError(
            "transcrição requer o extra 'media': python -m pip install 'nexo[media]' (Whisper local)"
        ) from exc
    whisper = WhisperModel(model, device="cpu", compute_type="int8")
    segments, _ = whisper.transcribe(str(path), vad_filter=True)
    return "\n".join(f"[{s.start:06.1f}s] {s.text.strip()}" for s in segments)


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise NexoError("vídeo requer ffmpeg instalado (brew install ffmpeg / winget install ffmpeg)")
    return exe


def extract_video(path: Path, dest_dir: Path, max_frames: int = 8) -> tuple[Path, list[Path]]:
    exe = ffmpeg()
    tmp = Path(tempfile.mkdtemp(prefix="nexo-video-"))
    audio = tmp / "audio.wav"
    subprocess.run(
        [exe, "-y", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(audio)], check=True
    )
    pattern = str(dest_dir / f"{_slug(path.stem)}-frame-%02d.jpg")
    subprocess.run(
        [
            exe,
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-vf",
            "select='gt(scene,0.3)',scale=1280:-1",
            "-vsync",
            "vfr",
            "-frames:v",
            str(max_frames),
            pattern,
        ],
        check=True,
    )
    frames = sorted(dest_dir.glob(f"{_slug(path.stem)}-frame-*.jpg"))
    return audio, frames


def _http_get_json(url: str, token: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"X-Figma-Token": token})  # noqa: S310 — host fixo https
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return dict(json.loads(resp.read().decode("utf-8")))


def figma_text(url: str, token: str) -> str:
    match = FIGMA_URL.match(url)
    if not match:
        raise NexoError(f"link Figma inválido: {url}")
    data = _http_get_json(f"https://api.figma.com/v1/files/{match.group(1)}", token)
    lines = [f"# Figma: {data.get('name', '')}"]

    def walk(node: dict[str, Any], depth: int) -> None:
        kind = node.get("type")
        if kind in ("CANVAS", "FRAME", "COMPONENT", "SECTION") and node.get("name"):
            lines.append(f"{'#' * min(depth + 1, 6)} {node['name']} ({kind.lower()})")
        if kind == "TEXT" and node.get("characters"):
            lines.append(f"- {str(node['characters']).strip()}")
        for child in node.get("children") or []:
            walk(child, depth + 1)

    walk(data.get("document") or {}, 0)
    return "\n".join(lines)


def ingest(
    inputs: list[str], dest_dir: Path, figma_token: str | None = None, whisper_model: str = "base"
) -> list[Ingested]:
    dest_dir.mkdir(parents=True, exist_ok=True)
    out: list[Ingested] = []
    for raw in inputs:
        path = Path(raw).expanduser()
        if FIGMA_URL.match(raw):
            if not figma_token:
                raise NexoError("link Figma requer token: nexo login figma")
            p, chars, red = _write_text(dest_dir, "figma", figma_text(raw, figma_token))
            out.append(Ingested("figma", raw, str(p), chars, red))
            continue
        if not path.is_file():
            p, chars, red = _write_text(dest_dir, "texto", raw)  # nome neutro: o conteúdo pode ter segredo
            out.append(Ingested("text", "texto informado", str(p), chars, red))
            continue
        ext = path.suffix.lower()
        name = _slug(path.stem)
        if ext in TEXT_EXT:
            p, chars, red = _write_text(dest_dir, name, path.read_text(encoding="utf-8", errors="replace"))
            out.append(Ingested("text", str(path), str(p), chars, red))
        elif ext in DOC_EXT:
            p, chars, red = _write_text(dest_dir, name, convert_document(path))
            out.append(Ingested("document", str(path), str(p), chars, red))
        elif ext in AUDIO_EXT:
            p, chars, red = _write_text(dest_dir, name + "-transcricao", transcribe(path, whisper_model))
            out.append(Ingested("audio", str(path), str(p), chars, red))
        elif ext in VIDEO_EXT:
            audio, frames = extract_video(path, dest_dir)
            p, chars, red = _write_text(dest_dir, name + "-transcricao", transcribe(audio, whisper_model))
            out.append(Ingested("video", str(path), str(p), chars, red, f"{len(frames)} quadro(s)"))
            out.extend(Ingested("image", str(path), str(f), 0, 0, "quadro do vídeo") for f in frames)
        elif ext in IMAGE_EXT:
            target = dest_dir / f"{name}{ext}"
            shutil.copy2(path, target)
            out.append(Ingested("image", str(path), str(target), 0, 0, "sem OCR: exige rota com visão"))
        else:
            raise NexoError(f"formato não suportado: {path.name}")
    return out

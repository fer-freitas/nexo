"""Do incidente à correção: normalização de alertas e incidentes e extração da pilha de erro.

Fontes aceitas (JSON): genérica, ServiceNow (`incident`), Sentry (evento) e Azure Monitor (esquema comum de alerta).
Tudo determinístico: nenhum LLM participa da leitura; o texto vindo de fora é tratado como dado pelo chamador.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

SOURCES = ("generic", "servicenow", "sentry", "azure")
CRITICAL = ("critical", "sev0", "sev1", "1", "p1", "fatal")


@dataclass(frozen=True)
class Frame:
    file: str
    line: int = 0
    function: str = ""


@dataclass
class Incident:
    id: str
    title: str
    source: str
    service: str = ""
    severity: str = ""
    opened_at: str = ""
    message: str = ""
    trace_id: str = ""
    link: str = ""
    frames: list[Frame] = field(default_factory=list)

    @property
    def critical(self) -> bool:
        return self.severity.strip().lower() in CRITICAL


# ------------------------------------------------------------------ pilha de erro

PY_FRAME = re.compile(r'File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<fn>[\w.<>]+))?')
NET_FRAME = re.compile(r"\bat (?P<fn>[\w.`<>\[\]]+)\([^)]*\)(?: in (?P<file>[^\n:]+?\.cs):line (?P<line>\d+))?")
JS_FRAME = re.compile(
    r"\bat (?:(?P<fn>[\w.$<>]+) \()?(?P<file>[^\s()]+?\.(?:[jt]sx?|mjs|cjs)):(?P<line>\d+)(?::\d+)?\)?"
)


def frames_from_text(text: str, limit: int = 30) -> list[Frame]:
    """Quadros da pilha em Python, .NET ou JavaScript/TypeScript, do mais interno para fora, sem duplicar."""
    out: list[Frame] = []
    seen: set[tuple[str, int]] = set()
    for pattern in (PY_FRAME, NET_FRAME, JS_FRAME):
        for m in pattern.finditer(text or ""):
            file = (m.group("file") or "").strip()
            if not file:
                continue
            line = int(m.group("line") or 0)
            if (file, line) in seen:
                continue
            seen.add((file, line))
            out.append(Frame(file, line, (m.group("fn") or "").strip()))
    return out[:limit]


# ------------------------------------------------------------------ fontes


def _str(value: Any) -> str:
    if isinstance(value, dict):  # ServiceNow com display_value
        return str(value.get("display_value") or value.get("value") or "")
    return "" if value is None else str(value)


def detect(data: dict[str, Any]) -> str:
    if "number" in data and ("short_description" in data or "sys_id" in data):
        return "servicenow"
    if "exception" in data or ("event_id" in data and "platform" in data):
        return "sentry"
    if "data" in data and isinstance(data.get("data"), dict) and "essentials" in data["data"]:
        return "azure"
    return "generic"


def normalize(data: dict[str, Any], source: str = "auto") -> Incident:
    kind = detect(data) if source == "auto" else source
    if kind not in SOURCES:
        raise ValueError(f"fonte desconhecida: {source} ({', '.join(SOURCES)})")
    if kind == "servicenow":
        text = "\n".join(_str(data.get(k)) for k in ("description", "comments", "work_notes"))
        return Incident(
            id=_str(data.get("number")),
            title=_str(data.get("short_description")),
            source=kind,
            service=_str(data.get("cmdb_ci") or data.get("business_service")),
            severity=_str(data.get("priority") or data.get("severity")).split(" ")[0],
            opened_at=_str(data.get("opened_at")),
            message=text[:4000],
            frames=frames_from_text(text),
        )
    if kind == "sentry":
        values = ((data.get("exception") or {}).get("values")) or []
        frames: list[Frame] = []
        messages = []
        for exc in values:
            messages.append(f"{exc.get('type', '')}: {exc.get('value', '')}".strip(": "))
            for f in reversed(((exc.get("stacktrace") or {}).get("frames")) or []):  # Sentry lista de fora para dentro
                file = str(f.get("filename") or f.get("abs_path") or "")
                if file and f.get("in_app", True):
                    frames.append(Frame(file, int(f.get("lineno") or 0), str(f.get("function") or "")))
        tags = {str(t[0]): str(t[1]) for t in data.get("tags") or [] if isinstance(t, (list, tuple)) and len(t) == 2}
        return Incident(
            id=str(data.get("event_id") or data.get("id") or ""),
            title=str(data.get("title") or (messages[0] if messages else "")),
            source=kind,
            service=tags.get("service", str(data.get("project") or "")),
            severity=str(data.get("level") or ""),
            opened_at=str(data.get("datetime") or data.get("timestamp") or ""),
            message="\n".join(messages)[:4000],
            trace_id=str(((data.get("contexts") or {}).get("trace") or {}).get("trace_id") or ""),
            frames=frames[:30],
        )
    if kind == "azure":
        ess = data["data"]["essentials"]
        ctx = data["data"].get("alertContext") or {}
        text = str(ctx.get("condition") or ctx)
        return Incident(
            id=str(ess.get("alertId") or "").rsplit("/", 1)[-1],
            title=str(ess.get("alertRule") or ess.get("description") or ""),
            source=kind,
            service=str((ess.get("configurationItems") or [""])[0]),
            severity=str(ess.get("severity") or "").lower(),
            opened_at=str(ess.get("firedDateTime") or ""),
            message=str(ess.get("description") or "")[:4000],
            frames=frames_from_text(text),
        )
    text = "\n".join(str(data.get(k) or "") for k in ("message", "error", "stacktrace", "stack", "log"))
    return Incident(
        id=str(data.get("id") or ""),
        title=str(data.get("title") or data.get("message") or "")[:200],
        source="generic",
        service=str(data.get("service") or ""),
        severity=str(data.get("severity") or ""),
        opened_at=str(data.get("opened_at") or data.get("timestamp") or ""),
        message=text[:4000],
        trace_id=str(data.get("trace_id") or ""),
        link=str(data.get("link") or data.get("url") or ""),
        frames=frames_from_text(text),
    )


def map_frames(frames: list[Frame], repo_files: list[str]) -> list[tuple[Frame, str]]:
    """Quadro da pilha → arquivo do repositório pelo maior sufixo de caminho em comum."""
    out: list[tuple[Frame, str]] = []
    for fr in frames:
        parts = fr.file.replace("\\", "/").lstrip("./").split("/")
        best, best_len = "", 0
        for rf in repo_files:
            rparts = rf.split("/")
            n = 0
            while n < min(len(parts), len(rparts)) and parts[-1 - n] == rparts[-1 - n]:
                n += 1
            if n > best_len:
                best, best_len = rf, n
        if best:
            out.append((fr, best))
    return out

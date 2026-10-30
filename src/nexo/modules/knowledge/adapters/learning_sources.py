"""Coletores determinísticos de aprendizado: PR, bugs, correções humanas, ADRs e incidentes."""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from pathlib import Path

from nexo.kernel.learning.model import RawEvent
from nexo.kernel.runtime import http, yamlfile
from nexo.kernel.workspace import git

MAX_TEXT = 4000


def pr_comments(repo: str, token: str, since: str | None) -> list[RawEvent]:
    url = f"https://api.github.com/repos/{repo}/pulls/comments?per_page=100&sort=created&direction=asc"
    if since:
        url += f"&since={urllib.parse.quote(since)}"
    body = http.request("GET", url, {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}).body
    return [
        RawEvent(
            "pr_comments",
            f"prc:{c['id']}",
            str(c.get("body", ""))[:MAX_TEXT],
            {"path": c.get("path"), "url": c.get("html_url"), "at": c.get("created_at")},
        )
        for c in body or []
        if str(c.get("body", "")).strip()
    ]


def local_bugs(project_root: Path) -> list[RawEvent]:
    out = []
    for path in sorted((project_root / ".nexo" / "bugs").glob("B-*.yaml")):
        data = yamlfile.load(path)
        text = "\n".join(
            [
                "Falhas:",
                *map(str, data.get("failures") or []),
                "Evidências:",
                *[f"{k}: {v}" for k, v in (data.get("evidence") or {}).items() if v],
            ]
        )
        out.append(
            RawEvent(
                "bugs",
                f"bug:{data.get('id')}",
                text[:MAX_TEXT],
                {"story": data.get("story"), "state": data.get("state"), "source": data.get("source")},
            )
        )
    return out


def human_fixes(repo: Path, limit: int = 200) -> list[RawEvent]:
    """Commits humanos que alteram arquivos entregues por commits do Nexo (trailer `Nexo-Run`)."""
    log = git.git(["log", "--all", f"-{limit}", "--format=%x1e%H%x1f%B%x1f", "--name-only"], repo, check=False)
    records = []
    for rec in log.split("\x1e"):
        if not rec.strip():
            continue
        parts = [*rec.split("\x1f", 2), "", ""]
        sha, body, names = parts[0], parts[1], parts[2]
        records.append((sha.strip(), body.strip(), [f for f in names.splitlines() if f.strip()]))
    nexo_files: set[str] = set()
    out: list[RawEvent] = []
    for sha, body, files in reversed(records):  # do mais antigo para o mais novo
        if "Nexo-Run:" in body:
            nexo_files.update(files)
            continue
        touched = [f for f in files if f in nexo_files]
        if touched and not body.startswith(("nexo:", "merge(")):
            diff = git.git(["show", "--format=", sha, "--", *touched], repo, check=False)[:MAX_TEXT]
            out.append(
                RawEvent(
                    "human_fixes",
                    f"fix:{sha[:12]}",
                    f"{body.splitlines()[0] if body else ''}\n{diff}",
                    {"files": touched},
                )
            )
    return out


def adrs(project_root: Path) -> list[RawEvent]:
    out = []
    for folder in ("docs/adr", "doc/adr", "adr", "spec/adr"):
        for path in sorted((project_root / folder).glob("*.md")):
            text = path.read_text(encoding="utf-8", errors="replace")
            digest = hashlib.sha256(text.encode()).hexdigest()[:10]
            out.append(
                RawEvent(
                    "adrs",
                    f"adr:{path.relative_to(project_root).as_posix()}:{digest}",
                    text[:MAX_TEXT],
                    {"path": path.relative_to(project_root).as_posix()},
                )
            )
    return out


def confluence(base_url: str, email: str, token: str, cql: str) -> list[RawEvent]:
    import base64

    auth = base64.b64encode(f"{email}:{token}".encode()).decode()
    url = f"{base_url.rstrip('/')}/rest/api/content/search?cql={urllib.parse.quote(cql)}&expand=body.storage&limit=50"
    body = http.request("GET", url, {"Authorization": f"Basic {auth}"}).body
    out = []
    for page in body.get("results", []):
        html = str(((page.get("body") or {}).get("storage") or {}).get("value", ""))
        text = re.sub(r"<[^>]+>", " ", html)
        out.append(
            RawEvent(
                "incidents",
                f"confluence:{page['id']}:{page.get('version', {}).get('number', 1)}",
                f"{page.get('title')}\n{text}"[:MAX_TEXT],
                {"system": "confluence", "title": page.get("title")},
            )
        )
    return out


def servicenow(instance: str, user: str, token: str, query: str) -> list[RawEvent]:
    import base64

    auth = base64.b64encode(f"{user}:{token}".encode()).decode()
    fields = "number,short_description,description,close_notes,cause,u_root_cause,sys_updated_on"
    url = (
        f"https://{instance}/api/now/table/incident?sysparm_query={urllib.parse.quote(query)}"
        f"&sysparm_fields={fields}&sysparm_limit=100"
    )
    body = http.request("GET", url, {"Authorization": f"Basic {auth}"}).body
    out = []
    for inc in body.get("result", []):
        parts = [
            inc.get("short_description"),
            inc.get("description"),
            inc.get("cause") or inc.get("u_root_cause"),
            inc.get("close_notes"),
        ]
        out.append(
            RawEvent(
                "incidents",
                f"snow:{inc['number']}:{inc.get('sys_updated_on', '')}",
                "\n".join(str(p) for p in parts if p)[:MAX_TEXT],
                {"system": "servicenow", "number": inc["number"]},
            )
        )
    return out


def dependency_diffs(repo: Path, limit: int = 100) -> dict[str, str]:
    """Diffs de arquivos de dependência por commit (para detectar adoção de bibliotecas)."""
    files = ["package.json", "pyproject.toml", "*.csproj", "requirements*.txt"]
    log = git.git(
        ["log", f"-{limit}", "--format=%x1e%H", "-p", "--", *files, *[f"**/{f}" for f in files]], repo, check=False
    )
    out: dict[str, str] = {}
    for chunk in log.split("\x1e"):
        chunk = chunk.strip()
        if chunk:
            sha, _, diff = chunk.partition("\n")
            out[sha[:12]] = diff
    return out

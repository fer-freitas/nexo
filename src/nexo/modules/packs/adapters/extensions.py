"""Extensões: descoberta por escopo, instalação canônica e transpilação para cada ferramenta."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from nexo.kernel.errors import NexoError
from nexo.kernel.extensions.model import Extension, ExtensionMeta, frontmatter_description, main_file
from nexo.kernel.runtime import yamlfile

PLURAL = {"skill": "skills", "hook": "hooks", "tool": "tools"}
PRECEDENCE = ("org", "squad", "project", "personal")  # a última vence, exceto quando a anterior está travada
MANAGED = "nexo-managed"
CLAUDE_EVENTS = {
    "session_start": "SessionStart",
    "pre_tool": "PreToolUse",
    "post_edit": "PostToolUse",
    "task_done": "Stop",
}


@dataclass(frozen=True)
class Locations:
    org: Path | None
    squad: Path | None
    project: Path | None
    personal: Path
    squad_name: str | None = None

    def root_for(self, scope: str) -> Path | None:
        return {"org": self.org, "squad": self.squad, "project": self.project, "personal": self.personal}[scope]

    def dir_for(self, scope: str, ext_type: str, name: str) -> Path:
        base = self.root_for(scope)
        if base is None:
            raise NexoError(f"escopo {scope} indisponível aqui")
        return base / PLURAL[ext_type] / name


def locations(home: Path, brain: Path | None, project_root: Path | None, squad: str | None) -> Locations:
    return Locations(
        org=brain / "extensions" if brain else None,
        squad=brain / "squads" / squad / "extensions" if brain and squad else None,
        project=project_root / ".nexo" / "extensions" if project_root else None,
        personal=home / "extensions" / "personal",
        squad_name=squad,
    )


META = "extension.yaml"
LEGACY_META = "nexo.yaml"  # nome anterior do produto


def meta_file(folder: Path) -> Path:
    """Metadados da extensão: `extension.yaml`, ou `nexo.yaml` em extensões criadas antes da troca de nome."""
    current = folder / META
    if current.is_file() or not (folder / LEGACY_META).is_file():
        return current
    return folder / LEGACY_META


def load(folder: Path, scope: str | None = None) -> Extension:
    meta_path = meta_file(folder)
    if not meta_path.is_file():
        raise NexoError(f"{folder}: extension.yaml ausente")
    raw = yamlfile.load(meta_path)
    if scope:
        raw["scope"] = scope
    try:
        meta = ExtensionMeta.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        raise NexoError(f"{meta_path}: {'.'.join(str(x) for x in first['loc'])}: {first['msg']}") from exc
    main = folder / main_file(meta.type)
    if not main.is_file():
        raise NexoError(f"{folder}: arquivo principal {main.name} ausente")
    if meta.type == "hook" and meta.hook is None:
        raise NexoError(f"{meta_path}: hook precisa da seção `hook` (event, matcher…)")
    if meta.type == "tool":
        try:
            json.loads(main.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise NexoError(f"{main}: JSON inválido") from exc
    return Extension(meta, str(folder), main.read_text(encoding="utf-8", errors="replace"))


def prepare_source(source: Path, ext_type: str, name: str | None, extra: dict[str, Any]) -> Path:
    """Aceita uma pasta canônica ou um arquivo solto (SKILL.md, hook.py, mcp.json) e devolve uma pasta canônica."""
    if source.is_dir():
        if not meta_file(source).is_file():
            main = source / main_file(ext_type)
            if not main.is_file():
                raise NexoError(f"{source}: esperado extension.yaml ou {main_file(ext_type)}")
            meta = {"name": name or source.name.lower(), "type": ext_type, **extra}
            if ext_type == "skill":
                meta.setdefault("description", frontmatter_description(main.read_text(encoding="utf-8")))
            yamlfile.dump(source / "extension.yaml", meta)
        return source
    if not source.is_file():
        raise NexoError(f"{source} não existe")
    import tempfile

    staging = Path(tempfile.mkdtemp(prefix="nexo-ext-")) / (name or source.stem.lower())
    staging.mkdir(parents=True)
    shutil.copy2(source, staging / main_file(ext_type))
    meta = {"name": name or source.stem.lower().replace("_", "-"), "type": ext_type, **extra}
    if ext_type == "skill":
        meta["description"] = meta.get("description") or frontmatter_description(source.read_text(encoding="utf-8"))
        if meta["name"] == "skill":
            meta["name"] = source.parent.name.lower()
    yamlfile.dump(staging / "extension.yaml", meta)
    return staging


def discover(locs: Locations) -> list[Extension]:
    """Extensões efetivas: a de maior precedência vence, salvo se uma de escopo acima estiver travada."""
    effective: dict[str, Extension] = {}
    for scope in PRECEDENCE:
        base = locs.root_for(scope)
        if base is None or not base.is_dir():
            continue
        for ext_type, plural in PLURAL.items():
            folder = base / plural
            if not folder.is_dir():
                continue
            for child in sorted(p for p in folder.iterdir() if p.is_dir() and meta_file(p).is_file()):
                try:
                    ext = load(child, scope)
                except NexoError:
                    continue
                if ext.meta.type != ext_type or not ext.meta.rolled_out_to(locs.squad_name):
                    continue  # tipo errado ou canário de outro squad
                current = effective.get(ext.key)
                if current is not None and current.meta.locked:
                    continue
                effective[ext.key] = ext
    return sorted(effective.values(), key=lambda e: (e.meta.type, e.meta.name))


def install_copy(ext: Extension, target: Path) -> Path:
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ext.path, target)
    meta = yamlfile.load(meta_file(target))
    meta["scope"] = ext.meta.scope
    yamlfile.dump(meta_file(target), meta)
    return target


# ---------------- transpilação


@dataclass
class ToolPaths:
    claude_user: Path  # ~/.claude
    copilot_user: Path  # ~/.copilot
    project: Path | None


@dataclass
class InstallReport:
    installed: dict[str, list[str]] = field(default_factory=dict)  # ferramenta → extensões nativas
    emulated: dict[str, list[str]] = field(default_factory=dict)
    unavailable: dict[str, list[str]] = field(default_factory=dict)

    def add(self, kind: str, tool: str, name: str) -> None:
        getattr(self, kind).setdefault(tool, []).append(name)


def _json_load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return dict(json.loads(path.read_text(encoding="utf-8")))
    except ValueError:
        return {}


def _json_dump(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _skill_dirs(ext: Extension, tool: str, paths: ToolPaths) -> Path:
    project_level = ext.meta.scope == "project" and paths.project is not None
    if tool == "claude":
        base = (paths.project / ".claude") if project_level and paths.project else paths.claude_user
        return base / "skills" / ext.meta.name
    base = (paths.project / ".github") if project_level and paths.project else paths.copilot_user
    return base / "skills" / ext.meta.name


def transpile(extensions: list[Extension], paths: ToolPaths, hook_command: list[str]) -> InstallReport:
    """Instala cada extensão no formato nativo de cada ferramenta; o que não é suportado fica emulado pelo Nexo."""
    report = InstallReport()
    claude_hooks: dict[Path, list[Extension]] = {}
    mcp_project: dict[str, Any] = {}
    mcp_copilot: dict[str, Any] = {}
    for ext in extensions:
        name = ext.meta.name
        for tool in ext.meta.targets:
            if ext.meta.type == "skill":
                if tool in ("claude", "copilot"):
                    target = _skill_dirs(ext, tool, paths)
                    install_copy(ext, target)
                    (target / f".{MANAGED}").write_text(ext.meta.scope, encoding="utf-8")
                    report.add("installed", tool, name)
                else:
                    report.add("emulated", tool, name)  # Devin: o Nexo injeta a descrição no prompt
            elif ext.meta.type == "hook":
                if tool == "claude" and ext.meta.hook and ext.meta.hook.event in CLAUDE_EVENTS:
                    base = (
                        (paths.project / ".claude")
                        if ext.meta.scope == "project" and paths.project
                        else paths.claude_user
                    )
                    claude_hooks.setdefault(base / "settings.json", []).append(ext)
                    report.add("installed", tool, name)
                else:
                    report.add("emulated", tool, name)  # o orquestrador executa o hook nos eventos
            else:  # tool (MCP)
                config = json.loads(ext.content)
                if tool == "claude" and paths.project is not None:
                    mcp_project[name] = config
                    report.add("installed", tool, name)
                elif tool == "copilot":
                    mcp_copilot[name] = config
                    report.add("installed", tool, name)
                else:
                    report.add("unavailable", tool, name)
    for settings_path, hooks in claude_hooks.items():
        _write_claude_hooks(settings_path, hooks, hook_command)
    if mcp_project and paths.project is not None:
        _merge_mcp(paths.project / ".mcp.json", mcp_project)
    if mcp_copilot:
        _merge_mcp(paths.copilot_user / "mcp-config.json", mcp_copilot)
    return report


def _write_claude_hooks(settings_path: Path, hooks: list[Extension], hook_command: list[str]) -> None:
    from nexo.kernel.runtime.system import shell_join

    settings = _json_load(settings_path)
    all_hooks: dict[str, list[dict[str, Any]]] = settings.get("hooks") or {}
    for event, entries in list(all_hooks.items()):  # remove entradas antigas geridas pelo Nexo
        all_hooks[event] = [
            e for e in entries if not any(MANAGED in str(h.get("command", "")) for h in e.get("hooks", []))
        ]
    for ext in hooks:
        if ext.meta.hook is None:
            continue
        event = CLAUDE_EVENTS[ext.meta.hook.event]
        command = shell_join([*hook_command, "hook", "exec", f"hook:{ext.meta.name}", "--claude"]) + f" # {MANAGED}"
        matcher = (
            "Edit|Write|MultiEdit"
            if ext.meta.hook.event == "post_edit" and ext.meta.hook.matcher == "*"
            else ext.meta.hook.matcher
        )
        all_hooks.setdefault(event, []).append(
            {
                "matcher": matcher,
                "hooks": [{"type": "command", "command": command, "timeout": max(1, ext.meta.hook.timeout_ms // 1000)}],
            }
        )
    settings["hooks"] = {k: v for k, v in all_hooks.items() if v}
    _json_dump(settings_path, settings)


def _merge_mcp(path: Path, servers: dict[str, Any]) -> None:
    data = _json_load(path)
    current = dict(data.get("mcpServers") or {})
    current.update(servers)
    data["mcpServers"] = current
    _json_dump(path, data)


def uninstall(ext_type: str, name: str, paths: ToolPaths) -> list[str]:
    removed: list[str] = []
    candidates = []
    if ext_type == "skill":
        for base in (
            paths.claude_user,
            paths.copilot_user,
            *([paths.project / ".claude", paths.project / ".github"] if paths.project else []),
        ):
            candidates.append(base / "skills" / name)
    for c in candidates:
        if (c / f".{MANAGED}").is_file():
            shutil.rmtree(c)
            removed.append(str(c))
    if ext_type == "tool":
        for mcp in ([paths.project / ".mcp.json"] if paths.project else []) + [paths.copilot_user / "mcp-config.json"]:
            data = _json_load(mcp)
            if name in (data.get("mcpServers") or {}):
                del data["mcpServers"][name]
                _json_dump(mcp, data)
                removed.append(str(mcp))
    return removed

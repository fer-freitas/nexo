"""Casos de uso de configuração (cascata org → squad → projeto → pessoal)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexo.kernel.config.cascade import Layer, Resolved, ensure_can_set, flatten, resolve, set_path, unset_path
from nexo.kernel.config.defaults import DATA_CLASSES, DEFAULTS, LANGS, PROFILES
from nexo.kernel.context import AppContext
from nexo.kernel.errors import NexoError, PolicyViolation
from nexo.kernel.runtime import yamlfile
from nexo.kernel.security.redaction import find

ALLOWED: dict[str, tuple[str, ...]] = {"lang": LANGS, "profile": PROFILES, "data_class": DATA_CLASSES}
WRITABLE_LEVELS = ("project", "personal")


@dataclass(frozen=True)
class LayerFile:
    level: str
    path: Path | None


def anchor() -> dict[str, Any]:
    """Âncora da org embutida no build corporativo (vazia = sandbox)."""
    from nexo.kernel.runtime.resources import load_yaml

    try:
        return dict(load_yaml("org-anchor.yaml") or {})
    except ValueError:
        return {}


def _brain_trusted(ctx: AppContext) -> bool:
    """Com âncora, org/squad só valem de um cérebro verificado com as chaves da âncora."""
    if not anchor():
        return True
    from nexo.kernel import brain as brain_adapter

    return bool((brain_adapter.cached_status(ctx.brain_dir) or {}).get("verified"))


def layer_files(ctx: AppContext, squad: str | None) -> list[LayerFile]:
    trusted = _brain_trusted(ctx)
    files = [LayerFile("org", ctx.brain_dir / "org" / "config.yaml" if trusted else None)]
    files.append(LayerFile("squad", ctx.brain_dir / "squads" / squad / "config.yaml" if squad and trusted else None))
    files.append(LayerFile("project", ctx.project_config()))
    files.append(LayerFile("personal", ctx.personal_config))
    return files


def load_layers(ctx: AppContext) -> list[Layer]:
    layers = [Layer.from_mapping("default", DEFAULTS, "embutido")]
    project_path = ctx.project_config()
    project_raw = yamlfile.load(project_path) if project_path else {}
    squad = project_raw.get("squad") if isinstance(project_raw.get("squad"), str) else None
    for lf in layer_files(ctx, squad):
        if lf.path is None:
            continue
        raw = project_raw if lf.level == "project" else yamlfile.load(lf.path)
        layers.append(Layer.from_mapping(lf.level, raw, str(lf.path)))
    return layers


def resolved(ctx: AppContext) -> Resolved:
    res = resolve(load_layers(ctx))
    pinned = anchor()
    if pinned:  # repositório e chaves do cérebro vêm do binário corporativo, não do dev
        brain = dict(res.values.get("brain") or {})
        for key in ("repo", "trusted_keys", "require_signature", "ref", "github_repo"):
            if key in pinned:
                brain[key] = pinned[key]
                res.origin[f"brain.{key}"] = "anchor"
        res.values["brain"] = brain
    return res


def validate_value(key: str, value: Any) -> None:
    allowed = ALLOWED.get(key)
    if allowed is not None and value not in allowed:
        raise NexoError(f"valor inválido para {key}: {value} (permitidos: {', '.join(allowed)})")
    if key == "max_parallel" and (not isinstance(value, int) or not 1 <= value <= 16):
        raise NexoError("max_parallel deve ser um inteiro entre 1 e 16")


def target_path(ctx: AppContext, level: str) -> Path:
    if level not in WRITABLE_LEVELS:
        raise NexoError(
            f"só é possível gravar nas camadas {', '.join(WRITABLE_LEVELS)}; org/squad mudam via PR no nexo-brain"
        )
    if level == "personal":
        return ctx.personal_config
    path = ctx.project_config()
    if path is None:
        raise NexoError("nenhum projeto encontrado; rode `nexo init`")
    return path


# Chaves que aceitam e-mail corporativo (contato não é segredo); segredos continuam recusados.
EMAIL_KEYS = (
    "notify.email.to",
    "notify.email.from",
    "notify.email.user",
    "tracker.jira.email",
    "docs.publish.confluence.email",
)


def set_value(ctx: AppContext, key: str, raw_value: str, level: str = "personal") -> Any:
    kinds = {f.kind for f in find(raw_value)}
    if key in EMAIL_KEYS:
        kinds.discard("email")
    if kinds:
        ctx.audit
        raise PolicyViolation("valor parece conter segredo ou dado pessoal; use o keyring (nexo login)")
    value = yamlfile.parse_scalar(raw_value)
    validate_value(key, value)
    layers = load_layers(ctx)
    try:
        ensure_can_set(layers, key, level)
    except PolicyViolation:
        ctx.audit("policy.violation", {"rule": "locked-key", "key": key, "level": level})
        raise
    path = target_path(ctx, level)
    data = yamlfile.load(path)
    old = flatten(data).get(key)
    set_path(data, key, value)
    yamlfile.dump(path, data)
    ctx.audit("config.set", {"key": key, "old": old, "new": value, "level": level, "path": str(path)})
    return value


def unset_value(ctx: AppContext, key: str, level: str = "personal") -> bool:
    path = target_path(ctx, level)
    data = yamlfile.load(path)
    removed = unset_path(data, key)
    if removed:
        yamlfile.dump(path, data)
        ctx.audit("config.unset", {"key": key, "level": level, "path": str(path)})
    return removed

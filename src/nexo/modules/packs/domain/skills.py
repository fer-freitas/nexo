"""Formato Agent Skills: pasta com `SKILL.md`, frontmatter com `name` e `description`, e `name` igual ao da pasta."""

from __future__ import annotations

import re

from ruamel.yaml import YAML, YAMLError

FRONT = re.compile(r"\A---\r?\n(.*?)\r?\n---", re.S)
NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
ALLOWED = frozenset({"name", "description", "license", "compatibility", "metadata", "allowed-tools"})
MAX_NAME = 64
MAX_DESCRIPTION = 1024


def check_skill(rel: str, text: str) -> list[str]:
    match = FRONT.match(text)
    if not match:
        return ["sem frontmatter"]
    try:
        meta = YAML(typ="safe", pure=True).load(match.group(1)) or {}
    except YAMLError:
        return ["frontmatter inválido"]
    out = []
    name = str(meta.get("name", ""))
    folder = rel.split("/")[-2] if "/" in rel else ""
    if not name:
        out.append("sem name")
    elif not NAME.match(name) or len(name) > MAX_NAME:
        out.append(f"name inválido: {name}")
    elif name != folder:
        out.append(f"name {name} difere da pasta {folder}")
    description = str(meta.get("description", ""))
    if not description:
        out.append("sem description")
    elif len(description) > MAX_DESCRIPTION:
        out.append("description longa demais")
    extra = sorted(set(meta) - ALLOWED)
    if extra:
        out.append(f"campos fora do padrão: {', '.join(extra)}")
    return out

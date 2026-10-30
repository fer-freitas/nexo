"""Consulta a registries oficiais e ao OSV para `check_dependency`. Somente HTTPS, somente leitura."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any
from xml.etree import ElementTree

TIMEOUT = 20
OSV_ECOSYSTEM = {"pypi": "PyPI", "npm": "npm", "nuget": "NuGet", "pub": "Pub", "maven": "Maven"}  # pub, maven


def _get(url: str) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:  # noqa: S310 — URLs https fixas
            return dict(json.loads(resp.read().decode("utf-8")))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _get_text(url: str) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:  # noqa: S310 — URLs https fixas
            return str(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _post(url: str, body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
        return dict(json.loads(resp.read().decode("utf-8")))


def _age_days(iso: str | None) -> int | None:
    if not iso:
        return None
    moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (datetime.now(UTC) - moment).days


def package_info(ecosystem: str, name: str) -> dict[str, Any]:
    quoted = urllib.parse.quote(name, safe="@/")
    if ecosystem == "pypi":
        data = _get(f"https://pypi.org/pypi/{quoted}/json")
        if data is None:
            return {"exists": False}
        info = data.get("info") or {}
        latest = str(info.get("version", ""))
        files = (data.get("releases") or {}).get(latest) or []
        uploaded = min((f.get("upload_time_iso_8601") for f in files if f.get("upload_time_iso_8601")), default=None)
        first = min(
            (
                f.get("upload_time_iso_8601")
                for rel in (data.get("releases") or {}).values()
                for f in rel
                if f.get("upload_time_iso_8601")
            ),
            default=uploaded,
        )
        license_ = str(info.get("license_expression") or info.get("license") or "")[:80]
        return {"exists": True, "latest": latest, "license": license_, "age_days": _age_days(first)}
    if ecosystem == "npm":
        data = _get(f"https://registry.npmjs.org/{quoted}")
        if data is None:
            return {"exists": False}
        latest = str((data.get("dist-tags") or {}).get("latest", ""))
        created = (data.get("time") or {}).get("created")
        return {
            "exists": True,
            "latest": latest,
            "license": str(data.get("license") or ""),
            "age_days": _age_days(created),
        }
    if ecosystem == "nuget":
        data = _get(f"https://api.nuget.org/v3-flatcontainer/{quoted.lower()}/index.json")
        if data is None:
            return {"exists": False}
        versions = data.get("versions") or []
        return {"exists": True, "latest": versions[-1] if versions else "", "license": "", "age_days": None}
    if ecosystem == "pub":  # pub.dev — licença vem das tags da pontuação do pacote
        data = _get(f"https://pub.dev/api/packages/{quoted}")
        if data is None:
            return {"exists": False}
        versions = data.get("versions") or []
        score = _get(f"https://pub.dev/api/packages/{quoted}/score") or {}
        licenses = [
            str(t).split(":", 1)[1]
            for t in score.get("tags") or []
            if str(t).startswith("license:") and str(t) not in ("license:fsf-libre", "license:osi-approved")
        ]
        return {
            "exists": True,
            "latest": str((data.get("latest") or {}).get("version", "")),
            "license": ", ".join(licenses).upper().replace("APACHE", "Apache"),
            "age_days": _age_days(versions[0].get("published") if versions else None),
        }
    if ecosystem == "maven":  # Maven Central, nome `grupo:artefato`
        group, _, artifact = name.partition(":")
        if not artifact:
            raise ValueError("pacote Maven no formato grupo:artefato (ex. com.google.guava:guava)")
        text = _get_text(f"https://repo1.maven.org/maven2/{group.replace('.', '/')}/{artifact}/maven-metadata.xml")
        if text is None:
            return {"exists": False}
        root = ElementTree.fromstring(text)  # noqa: S314 — XML do Maven Central, por HTTPS
        latest = root.findtext("versioning/release") or root.findtext("versioning/latest") or ""
        return {"exists": True, "latest": latest, "license": "", "age_days": None}
    raise ValueError(f"ecossistema não suportado: {ecosystem}")


def vulnerabilities(ecosystem: str, name: str, version: str | None) -> list[dict[str, str]]:
    body: dict[str, Any] = {"package": {"name": name, "ecosystem": OSV_ECOSYSTEM[ecosystem]}}
    if version:
        body["version"] = version
    data = _post("https://api.osv.dev/v1/query", body)
    out = []
    for vuln in data.get("vulns") or []:
        severity = str(((vuln.get("database_specific") or {}).get("severity")) or "unknown").lower()
        out.append({"id": str(vuln.get("id")), "severity": severity})
    return out

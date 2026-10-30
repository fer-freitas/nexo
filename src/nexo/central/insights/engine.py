"""Execução dos detectores: registro dos algoritmos, definições (baseline + cérebro) e gravação das ocorrências."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from nexo.central.db import CollectorDB
from nexo.central.insights.context import EvalCtx, Finding
from nexo.central.insights.cost import (
    budget_overrun,
    efficiency_trend,
    idle_license,
    invoice_divergence,
    tight_quota,
    token_hogs,
)
from nexo.central.insights.flow import adoption, estimate_calibration, human_bottleneck, outlier_story
from nexo.central.insights.platform import brain_change, telemetry_delay, threshold
from nexo.central.insights.quality import bad_route, escape, fragile_spec, quality_drop
from nexo.kernel.finops.insights import SILENCE_DAYS, Detector, parse_detector

ALGORITHMS: dict[str, Callable[[EvalCtx, Detector], list[Finding]]] = {
    "efficiency_trend": efficiency_trend,
    "outlier_story": outlier_story,
    "estimate_calibration": estimate_calibration,
    "budget_overrun": budget_overrun,
    "idle_license": idle_license,
    "bad_route": bad_route,
    "human_bottleneck": human_bottleneck,
    "escape": escape,
    "quality_drop": quality_drop,
    "brain_change": brain_change,
    "tight_quota": tight_quota,
    "fragile_spec": fragile_spec,
    "adoption": adoption,
    "invoice_divergence": invoice_divergence,
    "telemetry_delay": telemetry_delay,
    "threshold": threshold,
    "token_hogs": token_hogs,
}


def _yaml(text: str) -> dict[str, Any]:
    return dict(YAML(typ="safe", pure=True).load(text) or {})


def load_detectors(brain_dir: Path | None = None) -> list[Detector]:
    names = frozenset(ALGORITHMS)
    found: dict[str, dict[str, Any]] = {}
    base = resources.files("nexo.resources").joinpath("insights")
    for entry in sorted(base.iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".yaml"):
            data = _yaml(entry.read_text(encoding="utf-8"))
            found[str(data["id"])] = data
    folder = brain_dir or (
        Path(os.environ["NX_COLLECTOR_BRAIN_DIR"]) / "insights" if os.environ.get("NX_COLLECTOR_BRAIN_DIR") else None
    )
    if folder is not None and folder.is_dir():
        for path in sorted(folder.glob("*.yaml")):
            data = _yaml(path.read_text(encoding="utf-8"))
            if "id" in data:
                merged = {**found.get(str(data["id"]), {}), **data}
                merged["params"] = {
                    **dict(found.get(str(data["id"]), {}).get("params") or {}),
                    **dict(data.get("params") or {}),
                }
                found[str(data["id"])] = merged
    return [parse_detector(v, names) for _, v in sorted(found.items())]


def run(d: CollectorDB, now: datetime | None = None, detectors: list[Detector] | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    ctx = EvalCtx(d, now)
    summary: dict[str, Any] = {"evaluated": 0, "opened": 0, "updated": 0, "silenced": 0, "errors": {}}
    for det in detectors if detectors is not None else load_detectors():
        if not _enabled(d, det):
            continue
        summary["evaluated"] += 1
        try:
            findings = ALGORITHMS[det.algorithm](ctx, det)
        except (ValueError, KeyError, TypeError) as exc:
            summary["errors"][det.id] = str(exc)
            continue
        for f in findings:
            summary[_upsert(d, det, f, now)] += 1
    d.execute("DELETE FROM jobs WHERE job = 'insights.last'")
    d.execute(
        "INSERT INTO jobs (job, period, at, status, detail) VALUES ('insights.last', '-', ?, 'ok', ?)",
        (now.timestamp(), json.dumps({k: v for k, v in summary.items() if k != "errors"})),
    )
    return summary


def _enabled(d: CollectorDB, det: Detector) -> bool:
    """O portal pode ligar ou desligar cada detector, um a um."""
    row = d.one("SELECT enabled FROM insight_settings WHERE detector = ?", (det.id,))
    return bool(row["enabled"]) if row else det.enabled


def _upsert(d: CollectorDB, det: Detector, f: Finding, now: datetime) -> str:
    from nexo.central import flags

    if f.get("squad") and not flags.enabled(d, "insights", str(f["squad"])):  # desligado para o squad
        return "silenced"
    key = f"{det.id}|{f['scope_kind']}|{f['scope']}|{f['period']}"
    ts = now.timestamp()
    existing = d.one("SELECT id, state FROM insights WHERE key = ?", (key,))
    if existing is not None:
        if existing["state"] in ("aberto", "reconhecido"):
            d.execute(
                "UPDATE insights SET evidence = ?, last_seen = ? WHERE id = ?",
                (json.dumps(f["evidence"], ensure_ascii=False, default=str), ts, existing["id"]),
            )
            return "updated"
        return "silenced"
    muted = d.one(
        "SELECT id FROM insights WHERE detector = ? AND scope_kind = ? AND scope = ? AND state = 'descartado'"
        " AND updated_at >= ?",
        (det.id, f["scope_kind"], f["scope"], ts - SILENCE_DAYS * 86400),
    )
    if muted is not None:
        return "silenced"
    d.execute(
        "INSERT INTO insights (id, key, detector, title, scope_kind, scope, squad, dev_pid, purpose, severity, owner,"
        " action, evidence, state, reason, systemic, period, first_seen, last_seen, updated_at, updated_by, issue_url)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'aberto',NULL,?,?,?,?,?,'nexo',NULL) ON CONFLICT DO NOTHING",
        (
            uuid.uuid4().hex[:12],
            key,
            det.id,
            f"{det.title}: {f['detail']}"[:300],
            f["scope_kind"],
            f["scope"],
            f.get("squad"),
            f.get("dev_pid"),
            f.get("purpose"),
            det.severity,
            det.owner,
            det.action,
            json.dumps(f["evidence"], ensure_ascii=False, default=str),
            int(det.systemic),
            f["period"],
            ts,
            ts,
            ts,
        ),
    )
    return "opened"

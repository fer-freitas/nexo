"""Troca por arquivo (modo 3, sem API): exporta CSV no formato de importação e importa estados de volta."""

from __future__ import annotations

import csv
from pathlib import Path

from nexo.kernel.errors import NexoError
from nexo.kernel.tracker.mapping import Mapping, ProcessInfo
from nexo.modules.demands.adapters.base import ExternalRef, ExternalState, HistoricItem, TrackerItem

COLUMNS = ("ID", "Work Item Type", "Title", "State", "Tags", "Parent", "Nexo ID", "Description")


class FileExchange:
    name = "file"

    def __init__(self, folder: Path, mapping: Mapping) -> None:
        self.folder = folder
        self.mapping = mapping
        self.outbox = folder / "outbox.csv"

    def _rows(self) -> list[dict[str, str]]:
        if not self.outbox.is_file():
            return []
        with self.outbox.open(encoding="utf-8", newline="") as fh:
            return list(csv.DictReader(fh))

    def _write(self, rows: list[dict[str, str]]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        with self.outbox.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    def create(self, item: TrackerItem) -> ExternalRef:
        rows = [r for r in self._rows() if r["Nexo ID"] != item.local_id]
        target = self.mapping.target(item.state)
        rows.append(
            {
                "ID": "",
                "Work Item Type": self.mapping.external_type(item.type),
                "Title": item.title,
                "State": target.state or "",
                "Tags": "; ".join(["nexo", *target.tags]),
                "Parent": item.parent_ref or "",
                "Nexo ID": item.local_id,
                "Description": item.description[:2000],
            }
        )
        self._write(rows)
        return ExternalRef(f"file:{item.local_id}", str(self.outbox))

    def transition(self, ref: str, nexo_state: str, reason: str = "") -> None:
        local = ref.removeprefix("file:")
        rows = self._rows()
        target = self.mapping.target(nexo_state)
        for row in rows:
            if row["Nexo ID"] == local:
                tags = [t.strip() for t in row["Tags"].split(";") if t.strip()]
                row["Tags"] = "; ".join(self.mapping.merge_tags(tags, nexo_state))
                if target.state:
                    row["State"] = target.state
        self._write(rows)

    def comment(self, ref: str, text: str) -> None:
        with (self.folder / "comments.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{ref}\t{text}\n")

    def fetch(self, refs: list[str]) -> list[ExternalState]:
        inbox = self.folder / "inbox.csv"
        if not inbox.is_file():
            return []
        with inbox.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
        wanted = {r.removeprefix("file:") for r in refs}
        out = []
        for row in rows:
            local = row.get("Nexo ID") or ""
            if local in wanted:
                tags = tuple(t.strip() for t in (row.get("Tags") or "").split(";") if t.strip())
                out.append(ExternalState(f"file:{local}", row.get("State") or "", tags, ""))
        return out

    def history(self, since: str, until: str) -> list[HistoricItem]:
        raise NexoError("troca por arquivo não tem histórico: gere a linha de base no tracker de origem")

    def describe_process(self) -> ProcessInfo | None:
        return None

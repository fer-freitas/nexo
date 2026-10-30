"""Comandos `nexo meeting`: notas, gravação e revisão de cada dono."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

import typer

from nexo.modules.demands.app import meetings


def register_meetings(
    root: typer.Typer, ctx: Callable[[], Any], emit: Callable[[Any], None], show: Callable[..., None]
) -> None:
    meeting = typer.Typer(help="Reunião de refinamento", no_args_is_help=True)
    root.add_typer(meeting, name="meeting")

    @meeting.command("start")
    def meeting_start(
        did: str,
        type_: Annotated[str, typer.Option("--type", help="funcional ou tecnico")],
        with_: Annotated[str, typer.Option("--with", help="Pessoas, separadas por vírgula")] = "",
    ) -> None:
        """Abre a reunião e registra quem está presente."""
        data = meetings.start(ctx(), did, type_, [p.strip() for p in with_.split(",") if p.strip()])
        show(data, f"{data['id']} aberta para {data['demand']}: {', '.join(data['participants'])}")

    @meeting.command("note")
    def meeting_note(text: str) -> None:
        """Registra uma nota. Use prefixos: `critério:`, `técnico:`, `teste:`, `tarefa:`."""
        show(meetings.note(ctx(), text), "nota registrada")

    @meeting.command("import")
    def meeting_import(source: str) -> None:
        """Transcrição, ata, PDF, áudio ou vídeo vira texto redigido; o original não vai para o Git."""
        emit(meetings.import_file(ctx(), source))

    @meeting.command("close")
    def meeting_close(
        ai: Annotated[bool, typer.Option("--ai", help="A IA separa também as transcrições")] = False,
    ) -> None:
        """Separa o que foi dito por seção. Nada entra na spec e nada é aprovado."""
        data = meetings.close(ctx(), use_ai=ai)
        text = "\n".join(f"  {i['id']} [{i['section']}] {i['text']}" for i in data["items"])
        show(data, f"{data['id']} encerrada\n{text}\n  próximo: nexo meeting review {data['id']} --accept I-1")

    @meeting.command("review")
    def meeting_review(
        mid: str,
        accept: Annotated[list[str] | None, typer.Option("--accept")] = None,
        decline: Annotated[list[str] | None, typer.Option("--decline")] = None,
    ) -> None:
        """Cada dono aceita ou recusa o que é da própria seção."""
        data = meetings.review(ctx(), mid, accept or [], decline or [])
        show(data, f"{mid}: {len(data['decided'])} decidido(s), {len(data['pending'])} pendente(s)")

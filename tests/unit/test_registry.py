from __future__ import annotations

from nexo.cli.registry import COMMANDS, CURRENT_PHASE, GROUPS, match, specs_for, top_levels


def test_every_command_has_known_group_and_phase() -> None:
    for spec in COMMANDS:
        assert spec.group in GROUPS or spec.hidden
        assert 0 <= spec.phase <= CURRENT_PHASE
        assert spec.summary_pt and spec.summary_en


def test_paths_are_unique() -> None:
    paths = [s.path for s in COMMANDS]
    assert len(paths) == len(set(paths))


def test_match_longest() -> None:
    assert match(["plan", "feature", "88"]).path == "plan feature"  # type: ignore[union-attr]
    assert match(["plan", "x.mp4"]).path == "plan"  # type: ignore[union-attr]
    assert match(["skill", "add"]).path == "skill"  # type: ignore[union-attr]
    assert match(["nope"]) is None


def test_spec_documented_commands_exist() -> None:
    # Comandos citados no desenho funcional (02 §2.2).
    required = {
        "plan",
        "plan feature",
        "plan answer",
        "plan status",
        "plan approve",
        "plan graph",
        "run story",
        "run task",
        "status",
        "resume",
        "approve",
        "reject",
        "logs",
        "skill",
        "hook",
        "tool",
        "brain",
        "learn",
        "cost",
        "metrics",
        "estimate",
        "budget",
        "eval skill",
        "eval model",
        "eval report",
        "init",
        "doctor",
        "config get",
        "config set",
        "config list",
        "login",
        "install",
        "update",
        "uninstall",
        "version",
        "help",
    }
    assert required <= {s.path for s in COMMANDS}


def test_phase0_commands_available() -> None:
    assert CURRENT_PHASE >= 1
    available = {s.path for s in COMMANDS if s.available}
    assert {"init", "doctor", "config get", "logs", "audit verify", "version", "help"} <= available
    assert {"run task", "status", "resume", "mcp serve", "hook run"} <= available
    assert specs_for("config") and "config" in top_levels()


def test_every_old_name_points_to_a_visible_command() -> None:
    """comando fundido — o nome antigo continua valendo e a ajuda diz o nome novo."""
    visible = {s.path for s in COMMANDS if not s.hidden}
    tops = {p.split(" ")[0] for p in visible} | {" ".join(p.split(" ")[:2]) for p in visible}
    for spec in COMMANDS:
        if spec.alias_of:
            assert spec.hidden
            assert spec.alias_of in visible or " ".join(spec.alias_of.split(" ")[:2]) in tops, spec.path

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from nexo.kernel.runtime.resources import load_yaml
from nexo.kernel.workflow.engine import InvalidTransition, Workflow

TASK = Workflow.from_mapping(load_yaml("workflows/task.yaml"))
STORY = Workflow.from_mapping(load_yaml("workflows/story.yaml"))


def test_task_happy_path() -> None:
    s = TASK.initial
    for event in ("start", "complete"):
        s = TASK.next_state(s, event)
    assert s == "Concluido"


def test_task_block_and_resume() -> None:
    s = TASK.next_state("EmDev", "block")
    assert TASK.next_state(s, "start") == "EmDev"
    assert TASK.events_from("Bloqueado") == ["cancel", "escalate", "start"]


def test_invalid_transition() -> None:
    with pytest.raises(InvalidTransition):
        TASK.next_state("Concluido", "start")
    assert not TASK.can("Pronto", "complete")


def test_story_workflow_matches_spec() -> None:
    s = "Ideia"
    for event in ("plan", "approve_spec", "start", "implemented", "qa_passed", "needs_human", "approve"):
        s = STORY.next_state(s, event)
    assert s == "Concluido"
    assert STORY.next_state("EmDev", "spec_ambiguity") == "Refinamento"


def test_rejects_unknown_states() -> None:
    with pytest.raises(ValueError, match="desconhecidos"):
        Workflow.from_mapping(
            {"name": "x", "initial": "A", "states": ["A"], "transitions": [{"event": "e", "from": ["A"], "to": "B"}]}
        )
    with pytest.raises(ValueError, match="inicial"):
        Workflow.from_mapping({"name": "x", "initial": "Z", "states": ["A"], "transitions": []})


@given(st.lists(st.sampled_from(["start", "complete", "block", "escalate", "cancel", "reopen"]), max_size=30))
def test_property_never_leaves_known_states(events: list[str]) -> None:
    s = TASK.initial
    for event in events:
        if TASK.can(s, event):
            s = TASK.next_state(s, event)
        assert s in TASK.states

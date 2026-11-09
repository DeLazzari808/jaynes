"""A role whose stop holds the run: the step after it needs the person's answer, loaded by ``jaynes run``."""

from __future__ import annotations

from pydantic import BaseModel

from jaynes.role import Step, role, stop
from tests.stopped import Agent, Note

ROLE = role("held", agent="quick")
DRAFT = ROLE.step("draft", output=Note)


def question(_out: object, _seen: object) -> str:
    return "Is the draft what you asked?"


AGREE: Step[BaseModel] = ROLE.step("agree", needs=[DRAFT], stop=stop(0.2), question=question)
ROLE.step("final", output=Note, needs=[AGREE])

__all__ = ["ROLE", "Agent"]

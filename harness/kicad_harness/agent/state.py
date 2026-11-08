"""Shared AG-UI state between the agent and the frontend. See DESIGN.md, "Agent"."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CriterionStatus(BaseModel):
    id: str
    type: str
    passed: bool | None = None
    value: Any = None
    detail: str = ""


class GoalStatus(BaseModel):
    met: bool = False
    criteria: list[CriterionStatus] = Field(default_factory=list)


class RenderInfo(BaseModel):
    url: str
    caption: str = ""
    view: dict[str, Any] = Field(default_factory=dict)


class Highlight(BaseModel):
    uuids: list[str] = Field(default_factory=list)
    refs: list[str] = Field(default_factory=list)
    nets: list[str] = Field(default_factory=list)


class DesignState(BaseModel):
    """State shared with the UI.

    The frontend owns `design`, `selection`, `viewing`, `loop` and `supervised`.
    Tools update the rest and push snapshots to the UI after each change.
    """

    model_config = ConfigDict(extra="ignore")

    design: str | None = None
    branch: str | None = None
    head: str | None = None
    revision: int = 0
    # "schematic" until a board is laid out from the schematic, then "board".
    phase: str = "board"
    brief: str | None = None
    schematic_hash: str | None = None
    board_hash: str | None = None
    goal: GoalStatus | None = None
    last_render: RenderInfo | None = None
    playbook_phase: str | None = None
    highlight: Highlight | None = None
    selection: dict[str, Any] | None = None
    viewing: str | None = None
    loop: bool = False
    supervised: bool = False

"""Serializable controller-owned planning state.

The state records goals and evidence, not hidden chain-of-thought.  Methods are
immutable transformations so a checkpoint always sees a coherent snapshot.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

type MilestoneStatus = Literal["pending", "active", "completed", "abandoned"]

_DURABLE_NOTE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9 .,:;_+\-()]{0,499}\Z")
_SENSITIVE_NOTE_TERMS = frozenset(
    {"api key", "apikey", "authorization", "bearer", "cookie", "password", "secret", "token"}
)


def validate_durable_note(value: object) -> str:
    """Accept a deliberately narrow non-sensitive note suitable for checkpoints."""
    if not isinstance(value, str):
        raise ValueError("'note' must be a string")
    note = " ".join(value.split())
    lowered = note.casefold()
    if (
        not _DURABLE_NOTE_PATTERN.fullmatch(note)
        or any(term in lowered for term in _SENSITIVE_NOTE_TERMS)
        or "://" in note
        or "@" in note
    ):
        raise ValueError(
            "'note' must be 1-500 plain-text characters and cannot contain URLs, email "
            "addresses, credentials, or secret-like fields"
        )
    return note


class PlanMilestone(BaseModel):
    """One externally inspectable task milestone."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    status: MilestoneStatus = "pending"
    completed_at_step: int | None = Field(default=None, ge=0)


class EvidenceRecord(BaseModel):
    """Compact evidence retained beyond the rolling action-history window."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    step_number: int = Field(ge=0)
    kind: str = Field(default="fact", min_length=1)
    summary: str = Field(min_length=1, max_length=2000)
    source: str | None = Field(default=None, max_length=4000)


class PlanRevision(BaseModel):
    """Auditable replan event preserving why the old route was changed."""

    model_config = ConfigDict(frozen=True)

    revision: int = Field(ge=1)
    step_number: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000)
    strategy: str = Field(min_length=1)
    added_milestone_ids: tuple[str, ...] = ()


class PlanningState(BaseModel):
    """Goal, milestones, durable evidence, and revision history for one run."""

    model_config = ConfigDict(frozen=True)

    objective: str = Field(min_length=1)
    milestones: tuple[PlanMilestone, ...] = ()
    active_milestone_id: str | None = None
    evidence: tuple[EvidenceRecord, ...] = ()
    revisions: tuple[PlanRevision, ...] = ()

    @model_validator(mode="after")
    def validate_links(self) -> PlanningState:
        milestone_ids = [item.id for item in self.milestones]
        if len(milestone_ids) != len(set(milestone_ids)):
            raise ValueError("milestone ids must be unique")
        if self.active_milestone_id is not None and self.active_milestone_id not in milestone_ids:
            raise ValueError("active_milestone_id must reference a milestone")
        evidence_ids = [item.id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("evidence ids must be unique")
        return self

    @classmethod
    def create(cls, objective: str, milestone_descriptions: list[str]) -> PlanningState:
        milestones = tuple(
            PlanMilestone(
                id=f"m{index}",
                description=description.strip(),
                status="active" if index == 1 else "pending",
            )
            for index, description in enumerate(milestone_descriptions, start=1)
            if description.strip()
        )
        return cls(
            objective=objective.strip(),
            milestones=milestones,
            active_milestone_id=milestones[0].id if milestones else None,
        )

    def record_evidence(
        self,
        *,
        step_number: int,
        summary: str,
        source: str | None = None,
        kind: str = "fact",
    ) -> PlanningState:
        normalized = (kind.strip(), summary.strip(), source.strip() if source else None)
        if any((item.kind, item.summary, item.source) == normalized for item in self.evidence):
            return self
        record = EvidenceRecord(
            id=f"e{len(self.evidence) + 1}",
            step_number=step_number,
            kind=normalized[0],
            summary=normalized[1],
            source=normalized[2],
        )
        return self.model_copy(update={"evidence": (*self.evidence, record)})

    def complete_active(self, *, step_number: int) -> PlanningState:
        """Complete the active milestone and activate the next pending one."""
        if self.active_milestone_id is None:
            return self
        updated: list[PlanMilestone] = []
        completed = False
        for item in self.milestones:
            if item.id == self.active_milestone_id:
                updated.append(
                    item.model_copy(
                        update={"status": "completed", "completed_at_step": step_number}
                    )
                )
                completed = True
            else:
                updated.append(item)
        next_id = next(
            (item.id for item in updated if completed and item.status == "pending"),
            None,
        )
        if next_id is not None:
            updated = [
                item.model_copy(update={"status": "active"}) if item.id == next_id else item
                for item in updated
            ]
        return self.model_copy(
            update={"milestones": tuple(updated), "active_milestone_id": next_id}
        )

    def revise(
        self,
        *,
        step_number: int,
        reason: str,
        strategy: str,
        milestone_descriptions: list[str],
    ) -> PlanningState:
        """Abandon unfinished milestones and append a new revision plan."""
        revision_number = len(self.revisions) + 1
        retained = tuple(
            item if item.status == "completed" else item.model_copy(update={"status": "abandoned"})
            for item in self.milestones
        )
        added = tuple(
            PlanMilestone(
                id=f"r{revision_number}-m{index}",
                description=description.strip(),
                status="active" if index == 1 else "pending",
            )
            for index, description in enumerate(milestone_descriptions, start=1)
            if description.strip()
        )
        revision = PlanRevision(
            revision=revision_number,
            step_number=step_number,
            reason=reason.strip(),
            strategy=strategy.strip(),
            added_milestone_ids=tuple(item.id for item in added),
        )
        return self.model_copy(
            update={
                "milestones": (*retained, *added),
                "active_milestone_id": added[0].id if added else None,
                "revisions": (*self.revisions, revision),
            }
        )

    def prompt_summary(
        self,
        *,
        max_evidence: int = 12,
        max_durable_notes: int = 8,
    ) -> str:
        """Compact controller state without evicting deliberately retained notes.

        Ordinary tool evidence is a rolling cache, while ``durable_note`` records
        are explicit memory writes.  Selecting only the newest records allowed a
        long run's post-checkpoint tool traffic to push every retained note out of
        the planner prompt even though the notes remained in the checkpoint.  Keep
        a bounded, dedicated durable slice and spend the remaining budget on recent
        evidence.
        """
        if max_evidence < 0 or max_durable_notes < 0:
            raise ValueError("prompt evidence limits must be non-negative")
        active = next(
            (item.description for item in self.milestones if item.id == self.active_milestone_id),
            "none",
        )
        lines = [f"OBJECTIVE: {self.objective}", f"ACTIVE MILESTONE: {active}"]

        durable = [item for item in self.evidence if item.kind == "durable_note"]
        durable_budget = min(max_durable_notes, max_evidence)
        durable = durable[-durable_budget:] if durable_budget else []
        recent_budget = max(max_evidence - len(durable), 0)
        recent = [item for item in self.evidence if item.kind != "durable_note"]
        recent = recent[-recent_budget:] if recent_budget else []

        if durable:
            lines.append("DURABLE NOTES (retain and use when the task asks for them):")
            lines.extend(
                f"- [{item.id}, step {item.step_number}] {item.summary}"
                + (f" ({item.source})" if item.source else "")
                for item in durable
            )
        if recent:
            lines.append("RECENT EVIDENCE:")
            lines.extend(
                f"- [{item.id}, step {item.step_number}] {item.summary}"
                + (f" ({item.source})" if item.source else "")
                for item in recent
            )
        return "\n".join(lines)


__all__ = [
    "EvidenceRecord",
    "MilestoneStatus",
    "PlanMilestone",
    "PlanRevision",
    "PlanningState",
    "validate_durable_note",
]

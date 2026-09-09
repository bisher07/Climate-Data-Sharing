"""The reasoning interface — the single seam between agents and a language model.

The project has two seams. `ledger/port.py` is where agents meet the
blockchain; this is where they meet an LLM. They are deliberately the same
shape: a narrow Protocol, a frozen result type, and a failure that is a value
rather than an exception.

Section 19 lists "treat the LLM as the security mechanism" as a mistake to
avoid, and Section 16 says agents propose while Fabric enforces. Three
properties here are what make that true structurally rather than by
convention:

**Advice cannot approve.** `Advice` has no `approved` field, in the same way
that `AccessRequestSubmission` has no `requester_org` field. There is nowhere
for a model to put an approval, so no amount of prompt injection, model error
or misuse can produce one. The most a model can do is add a reason to refuse.

**Advice only ever tightens.** `Decision.tightened_by` folds advice into a
deterministic decision. It can turn an approval into a refusal and can add
concerns; it can never drop a deterministic reason or turn a refusal into an
approval. A compromised model can therefore make the system more cautious,
never more permissive — which is the safe direction for the failure to run in.

**A model is optional and may fail.** An agent with no advisor behaves exactly
as it does today, and an advisor that times out or errors degrades to
`Advice.unavailable()` rather than raising. Ingestion must not stop because a
language model is down, so the deterministic path is always the whole path
plus, never the whole path minus.

Two further rules are about evidence rather than authority.

**Every consultation is auditable.** `Agent.consult` records the hash of
exactly what was disclosed to the model, so a reviewer can later prove what the
model was and was not shown.

**Disclosure is explicit.** `AdvisoryRequest.disclosed` is built field by field
by the calling agent. Never pass a raw `ObservationRecord`: Section 8 keeps raw
data off-chain, and a hosted model is no more "on-premise" than a ledger is.
Org2's compliance-sensitive air-quality readings in particular must not reach a
third-party API just because an agent wanted a sentence of prose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

__all__ = ["Advice", "Advisor", "AdvisorError", "AdvisoryRequest"]


class AdvisorError(Exception):
    """The model could not be reached or answered at all.

    Agents do not catch this themselves — `Agent.consult` turns it into
    `Advice.unavailable()`, so a model outage is a normal, countable outcome
    rather than a crash in the middle of ingestion.
    """


@dataclass(frozen=True, slots=True)
class AdvisoryRequest:
    """One question put to a model.

    `disclosed` is the whole of what the model may see. Build it explicitly
    from the fields the question actually needs — passing a whole record
    "because it is easier" is how regulated data ends up in someone's API logs.
    """

    task: str
    question: str
    disclosed: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Advice:
    """What a model is allowed to contribute.

    There is deliberately no `approved` field, and adding one would undo the
    property the rest of this module exists to protect.
    """

    concerns: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    explanation: str | None = None
    model: str | None = None
    available: bool = True

    @classmethod
    def unavailable(cls) -> Advice:
        """No model answered. Contributes nothing in either direction."""
        return cls(available=False)

    def __bool__(self) -> bool:
        return self.available


@runtime_checkable
class Advisor(Protocol):
    """What an agent is given. Two members and no more."""

    @property
    def model(self) -> str:
        """Identifies the model, for the audit trail: `"claude-sonnet-5"`."""

    def advise(self, request: AdvisoryRequest) -> Advice:
        """Answer a question. Raise `AdvisorError` if the model is unreachable."""

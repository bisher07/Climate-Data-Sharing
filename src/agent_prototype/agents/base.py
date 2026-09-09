"""Agent base class and audit trail.

Section 12 says AI agents are not trusted authorities, and two things here
follow from that. An agent is handed a `LedgerClient` and nothing else, so it
has no way to reach past the chaincode's rules. And every decision it makes is
appended to an audit trail, so a reviewer can reconstruct why a transaction was
proposed regardless of whether the ledger accepted it.

An agent's own approval is therefore evidence, not authority. The distinction
matters most when an agent is wrong.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from agent_prototype.ledger.port import LedgerClient, TxReceipt
from agent_prototype.reasoning.port import Advice, Advisor, AdvisorError, AdvisoryRequest
from agent_prototype.shared.utilities.canonical import hash_payload
from agent_prototype.shared.utilities.clock import Clock, SystemClock

__all__ = ["Agent", "AuditRecord", "Decision"]


@dataclass(frozen=True, slots=True)
class Decision:
    """An agent's judgement about whether it is willing to proceed.

    `reasons` explain a refusal. `concerns` are recorded but do not block:
    Section 9's principle is to make doubt visible rather than to suppress the
    record it attaches to.
    """

    approved: bool
    reasons: tuple[str, ...] = ()
    concerns: tuple[str, ...] = ()
    explanation: str | None = None

    def __bool__(self) -> bool:
        return self.approved

    @property
    def summary(self) -> str:
        if self.approved:
            return "approved" + (f" with concerns: {'; '.join(self.concerns)}" if self.concerns else "")
        return "; ".join(self.reasons) or "refused"

    def tightened_by(self, advice: Advice) -> Decision:
        """Fold a model's advice into a deterministic decision.

        The fold only ever tightens: advice can add reasons and concerns, and
        can turn an approval into a refusal, but it cannot drop a deterministic
        reason and cannot turn a refusal into an approval. A model that is
        wrong, or one that has been talked into something by hostile text in a
        justification field, can therefore only make this agent more cautious.

        That asymmetry is the whole reason an LLM is allowed near a decision at
        all (Section 19), so it lives here rather than in each agent, where one
        of them would eventually get it wrong.
        """
        return Decision(
            approved=self.approved and not advice.reasons,
            reasons=self.reasons + advice.reasons,
            concerns=self.concerns + advice.concerns,
            explanation=advice.explanation or self.explanation,
        )


@dataclass(frozen=True, slots=True)
class AuditRecord:
    """One entry of evidence. `input_hash` lets a reviewer confirm that the
    thing the agent judged is the thing that was later submitted."""

    agent_id: str
    org_id: str
    action: str
    timestamp: datetime
    input_hash: str
    outcome: str
    detail: dict[str, Any] = field(default_factory=dict)


class Agent:
    """An organization-scoped agent with a controlled ledger interface."""

    def __init__(
        self,
        agent_id: str,
        ledger: LedgerClient,
        *,
        clock: Clock | None = None,
        advisor: Advisor | None = None,
    ) -> None:
        self.agent_id = agent_id
        self.ledger = ledger
        self.clock = clock or SystemClock()
        self.advisor = advisor
        self.audit_trail: list[AuditRecord] = []

    @property
    def org_id(self) -> str:
        """The organization this agent acts for, taken from its connection."""
        return self.ledger.org_id

    def record(self, action: str, payload: Any, outcome: str, **detail: Any) -> AuditRecord:
        entry = AuditRecord(
            agent_id=self.agent_id,
            org_id=self.org_id,
            action=action,
            timestamp=self.clock.now(),
            input_hash=hash_payload(payload),
            outcome=outcome,
            detail={k: v for k, v in detail.items() if v is not None},
        )
        self.audit_trail.append(entry)
        return entry

    def consult(self, request: AdvisoryRequest) -> Advice:
        """Ask the model, if there is one, and record what it was shown.

        Never raises. An agent with no advisor, and an agent whose advisor is
        unreachable, both get `Advice.unavailable()` — which contributes
        nothing in either direction, so the deterministic path is unaffected.

        The audit entry hashes `disclosed`, so a reviewer can later prove
        exactly what the model was and was not shown. `explanation` is left out
        of the entry on purpose: model prose is evidence for a human, and
        belongs in the record the agent builds, not scattered through the trail.
        """
        if self.advisor is None:
            return Advice.unavailable()

        try:
            advice = self.advisor.advise(request)
        except AdvisorError as exc:
            self.record(
                "consult", request.disclosed, "UNAVAILABLE",
                task=request.task, model=self.advisor.model, error=str(exc),
            )
            return Advice.unavailable()

        self.record(
            "consult", request.disclosed, "ADVISED",
            task=request.task,
            model=advice.model or self.advisor.model,
            reasons=list(advice.reasons) or None,
            concerns=list(advice.concerns) or None,
        )
        return advice

    def record_receipt(self, action: str, payload: Any, receipt: TxReceipt) -> TxReceipt:
        """Log a ledger outcome, whichever way it went."""
        self.record(
            action,
            payload,
            str(receipt.status),
            tx_id=receipt.tx_id,
            message=receipt.message,
            endorsing_orgs=list(receipt.endorsing_orgs) or None,
        )
        return receipt

    def __repr__(self) -> str:
        return f"{type(self).__name__}(agent_id={self.agent_id!r}, org={self.org_id!r})"

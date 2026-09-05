"""Org1 ingestion workflow — Scenario A of Section 6.

    Org1 → Ingestion Agent → Observation Anchor → Org1 endorsement → Ledger

This is the ordering from Section 11 made explicit: screen, review, submit. The
three stages stay separable on purpose, so an experiment can disable the agent
review and confirm the ledger still refuses what it should — which is the
comparison Section 15's baselines are asking for.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agent_prototype.agents.base import Decision
from agent_prototype.agents.org1.ingestion_provenance import (
    IngestionProvenanceAgent,
    PreparedAnchor,
    ValidationFailure,
)
from agent_prototype.agents.org1.policy_endorsement import PolicyEndorsementAgent
from agent_prototype.ledger.port import TxReceipt
from agent_prototype.shared.models import Calibration, ObservationRecord

__all__ = ["Org1IngestionPipeline", "PipelineResult", "Stage"]


class Stage(StrEnum):
    """Where an observation stopped. Countable, for evaluation metrics."""

    SCREENING = "SCREENING"
    POLICY_REVIEW = "POLICY_REVIEW"
    LEDGER_REJECTED = "LEDGER_REJECTED"
    COMMITTED = "COMMITTED"


@dataclass(frozen=True, slots=True)
class PipelineResult:
    observation_id: str
    stage: Stage
    prepared: PreparedAnchor | None = None
    decision: Decision | None = None
    receipt: TxReceipt | None = None
    error: str | None = None

    @property
    def committed(self) -> bool:
        return self.stage is Stage.COMMITTED


class Org1IngestionPipeline:
    def __init__(
        self, ingestion: IngestionProvenanceAgent, policy: PolicyEndorsementAgent
    ) -> None:
        if ingestion.org_id != policy.org_id:
            raise ValueError(
                f"agents belong to different organizations: {ingestion.org_id} and {policy.org_id}"
            )
        self.ingestion = ingestion
        self.policy = policy

    def ingest(
        self,
        observation_id: str,
        record: ObservationRecord,
        calibration: Calibration,
        *,
        uri: str | None = None,
        doi: str | None = None,
    ) -> PipelineResult:
        try:
            prepared = self.ingestion.prepare_anchor(
                observation_id, record, calibration, uri=uri, doi=doi
            )
        except ValidationFailure as exc:
            return PipelineResult(observation_id, Stage.SCREENING, error=str(exc))

        decision = self.policy.review_anchor(prepared)
        if not decision.approved:
            return PipelineResult(
                observation_id, Stage.POLICY_REVIEW,
                prepared=prepared, decision=decision, error=decision.summary,
            )

        receipt = self.ingestion.submit_anchor(prepared)
        return PipelineResult(
            observation_id,
            Stage.COMMITTED if receipt.committed else Stage.LEDGER_REJECTED,
            prepared=prepared,
            decision=decision,
            receipt=receipt,
            error=receipt.message,
        )

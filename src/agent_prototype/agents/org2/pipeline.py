"""Org2 validation workflow — Scenarios B and C of Section 6.

    Sensor data → Ingestion → Quality/Validation → Policy → Ledger

Two entry points, because the two scenarios need different inputs. `ingest`
handles Org2's own reading end to end (Scenario B). `compare` takes a second
reading from another organization and raises a divergence flag if the two
disagree past a threshold (Scenario C) — it is separate because a reference
reading is not always available, and because Org2 can legitimately anchor a
reading it never compares against anything.

The stages stay separable for the same reason Org1's are: an experiment can
disable agent review and confirm the ledger still refuses what it should.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agent_prototype.agents.base import Decision
from agent_prototype.agents.org2.ingestion_provenance import (
    IngestionProvenanceAgent,
    PreparedAnchor,
    ValidationFailure,
)
from agent_prototype.agents.org2.policy_endorsement import PolicyEndorsementAgent
from agent_prototype.agents.org2.quality_validation import QualityValidationAgent
from agent_prototype.ledger.port import TxReceipt
from agent_prototype.shared.models import (
    Calibration,
    DivergenceFlagRequest,
    ObservationRecord,
    QualityRecordRequest,
)

__all__ = ["Org2ValidationPipeline", "PipelineResult", "Stage"]


class Stage(StrEnum):
    """Where an observation stopped. Countable, for evaluation metrics."""

    SCREENING = "SCREENING"
    POLICY_REVIEW = "POLICY_REVIEW"
    LEDGER_REJECTED = "LEDGER_REJECTED"
    COMMITTED = "COMMITTED"
    WITHIN_TOLERANCE = "WITHIN_TOLERANCE"  # `compare` only: nothing to flag


@dataclass(frozen=True, slots=True)
class PipelineResult:
    observation_id: str
    stage: Stage
    prepared: PreparedAnchor | None = None
    quality: QualityRecordRequest | None = None
    divergence: DivergenceFlagRequest | None = None
    decision: Decision | None = None
    receipt: TxReceipt | None = None
    quality_receipt: TxReceipt | None = None
    error: str | None = None

    @property
    def committed(self) -> bool:
        return self.stage is Stage.COMMITTED


class Org2ValidationPipeline:
    def __init__(
        self,
        ingestion: IngestionProvenanceAgent,
        quality: QualityValidationAgent,
        policy: PolicyEndorsementAgent,
    ) -> None:
        orgs = {ingestion.org_id, quality.org_id, policy.org_id}
        if len(orgs) != 1:
            raise ValueError(f"agents belong to different organizations: {sorted(orgs)}")
        self.ingestion = ingestion
        self.quality = quality
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
        """Screen, score, review, submit.

        A low quality score never stops the record: Section 9's principle is to
        make doubt visible rather than suppress what it attaches to, so the
        score is published alongside the anchor rather than gating it.
        """
        try:
            prepared = self.ingestion.prepare_anchor(
                observation_id, record, calibration, uri=uri, doi=doi
            )
        except ValidationFailure as exc:
            return PipelineResult(observation_id, Stage.SCREENING, error=str(exc))

        scored = self.quality.assess_quality(observation_id, record, calibration)

        decision = self.policy.review_anchor(prepared)
        if not decision.approved:
            return PipelineResult(
                observation_id, Stage.POLICY_REVIEW,
                prepared=prepared, quality=scored, decision=decision,
                error=decision.summary,
            )

        receipt = self.ingestion.submit_anchor(prepared)
        if not receipt.committed:
            return PipelineResult(
                observation_id, Stage.LEDGER_REJECTED,
                prepared=prepared, quality=scored, decision=decision,
                receipt=receipt, error=receipt.message,
            )

        # Only now can the quality record be reviewed: it is a claim about an
        # anchor, and until the anchor commits there is nothing on the ledger
        # for the reviewer to check it against.
        quality_decision = self.policy.review_quality_record(scored)
        quality_receipt = (
            self.quality.submit_quality_record(scored) if quality_decision.approved else None
        )

        return PipelineResult(
            observation_id, Stage.COMMITTED,
            prepared=prepared, quality=scored, decision=decision,
            receipt=receipt, quality_receipt=quality_receipt,
            error=None if quality_decision.approved else quality_decision.summary,
        )

    def compare(
        self,
        org2_observation_id: str,
        org2_record: ObservationRecord,
        *,
        reference_org: str,
        reference_observation_id: str,
        reference_record: ObservationRecord,
        variable: str,
        threshold: float,
    ) -> PipelineResult:
        """Compare one variable against another organization's reading and,
        if they disagree past the threshold, propose a divergence flag."""
        flag = self.quality.detect_divergence(
            org2_observation_id,
            org2_record,
            reference_org,
            reference_observation_id,
            reference_record,
            variable=variable,
            threshold=threshold,
        )
        if flag is None:
            return PipelineResult(org2_observation_id, Stage.WITHIN_TOLERANCE)

        decision = self.policy.review_divergence_flag(flag)
        if not decision.approved:
            return PipelineResult(
                org2_observation_id, Stage.POLICY_REVIEW,
                divergence=flag, decision=decision, error=decision.summary,
            )

        receipt = self.quality.submit_divergence_flag(flag)
        return PipelineResult(
            org2_observation_id,
            Stage.COMMITTED if receipt.committed else Stage.LEDGER_REJECTED,
            divergence=flag, decision=decision, receipt=receipt, error=receipt.message,
        )

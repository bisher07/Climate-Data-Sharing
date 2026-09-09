"""Org2 Quality / Validation Agent.

The primary validation agent in the system (Section 3). It does two
independent things:

* Scores Org2's own reading on its own merits (`assess_quality`).
* Compares Org2's reading against a reference reading from another
  organization and, if they disagree past a threshold, raises that
  disagreement as evidence (`detect_divergence`).

Both are deterministic. Section 9 is explicit that this agent is not a
neutral third party and that disagreement is not automatically proof that
either side is wrong — the job here is only to make disagreement visible,
traceable and auditable, never to adjudicate it.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from agent_prototype.agents.base import Agent
from agent_prototype.agents.org2.ingestion_provenance import PLAUSIBLE_RANGES
from agent_prototype.ledger.port import Fn, TxReceipt
from agent_prototype.shared.models import (
    Calibration,
    DivergenceFlagRequest,
    ObservationRecord,
    QualityRecordRequest,
)
from agent_prototype.shared.utilities.canonical import hash_payload

__all__ = ["QualityValidationAgent", "DEFAULT_MAX_CALIBRATION_AGE"]

DEFAULT_VALIDATION_METHOD = "range-midpoint-v1"
DEFAULT_MAX_CALIBRATION_AGE = timedelta(days=365)


class QualityValidationAgent(Agent):
    def __init__(self, agent_id: str, ledger, *, clock=None,
                 max_calibration_age: timedelta = DEFAULT_MAX_CALIBRATION_AGE) -> None:
        super().__init__(agent_id, ledger, clock=clock)
        self.max_calibration_age = max_calibration_age

    # ------------------------------------------------------------------
    # Scoring Org2's own reading
    # ------------------------------------------------------------------

    def assess_quality(
        self,
        observation_id: str,
        record: ObservationRecord,
        calibration: Calibration,
        *,
        method: str = DEFAULT_VALIDATION_METHOD,
    ) -> QualityRecordRequest:
        """Score how plausible the reading looks, and how much to trust it.

        `quality_score` reflects the reading itself: how close each variable
        sits to the centre of its plausible range, averaged. A value near the
        edge of what is physically possible scores lower without being
        rejected outright — screening already caught anything impossible.

        `confidence` reflects the measurement pipeline rather than the
        reading: it degrades as the instrument's calibration ages, since a
        stale calibration makes any reading, however plausible, less
        trustworthy.
        """
        quality_score = self._range_midpoint_score(record)
        confidence = self._calibration_confidence(calibration)

        request = QualityRecordRequest(
            observation_id=observation_id,
            quality_score=quality_score,
            confidence=confidence,
            validation_method=method,
            agent_id=self.agent_id,
            validated_at=self.clock.now(),
        )
        self.record(
            "assess_quality", record, "SCORED",
            observation_id=observation_id,
            quality_score=quality_score,
            confidence=confidence,
        )
        return request

    def submit_quality_record(self, request: QualityRecordRequest) -> TxReceipt:
        return self.record_receipt(
            "submit_quality_record",
            request,
            self.ledger.submit(Fn.CREATE_QUALITY_RECORD, request=request.model_dump(mode="json")),
        )

    def _range_midpoint_score(self, record: ObservationRecord) -> float:
        scores = []
        for name, variable in record.variables.items():
            low, high = PLAUSIBLE_RANGES.get(name, (variable.value, variable.value))
            if high == low:
                scores.append(1.0)
                continue
            midpoint = (low + high) / 2
            half_range = (high - low) / 2
            closeness = 1.0 - abs(variable.value - midpoint) / half_range
            scores.append(max(0.0, min(1.0, closeness)))
        return sum(scores) / len(scores) if scores else 0.0

    def _calibration_confidence(self, calibration: Calibration) -> float:
        age = self.clock.now() - calibration.calibrated_at
        if age <= self.max_calibration_age:
            return 1.0
        overdue = (age - self.max_calibration_age) / self.max_calibration_age
        return max(0.0, 1.0 - overdue)

    # ------------------------------------------------------------------
    # Comparing against a reference reading from another organization
    # ------------------------------------------------------------------

    def detect_divergence(
        self,
        org2_observation_id: str,
        org2_record: ObservationRecord,
        reference_org: str,
        reference_observation_id: str,
        reference_record: ObservationRecord,
        *,
        variable: str,
        threshold: float,
    ) -> DivergenceFlagRequest | None:
        """Compare one shared variable between Org2's reading and a
        reference reading. Returns `None` when there is nothing to flag —
        either the variable is not present on both sides, or the difference
        is within tolerance.
        """
        org2_value = org2_record.variables.get(variable)
        reference_value = reference_record.variables.get(variable)
        if org2_value is None or reference_value is None:
            self.record(
                "detect_divergence", org2_record, "SKIPPED",
                reason=f"{variable!r} not present on both readings",
                org2_observation_id=org2_observation_id,
            )
            return None

        metric = abs(org2_value.value - reference_value.value)
        if metric <= threshold:
            self.record(
                "detect_divergence", org2_record, "WITHIN_TOLERANCE",
                variable=variable, divergence_metric=metric, threshold=threshold,
                org2_observation_id=org2_observation_id,
            )
            return None

        evidence_hash = hash_payload(
            {
                "variable": variable,
                "org2_observation_id": org2_observation_id,
                "org2_value": org2_value.value,
                "reference_org": reference_org,
                "reference_observation_id": reference_observation_id,
                "reference_value": reference_value.value,
            }
        )
        request = DivergenceFlagRequest(
            flag_id=f"DF-{org2_observation_id}",
            org2_observation_id=org2_observation_id,
            reference_observation_id=reference_observation_id,
            reference_org=reference_org,
            divergence_metric=metric,
            threshold=threshold,
            evidence_hash=evidence_hash,
            detected_at=self.clock.now(),
        )
        self.record(
            "detect_divergence", org2_record, "FLAGGED",
            variable=variable, divergence_metric=metric, threshold=threshold,
            org2_observation_id=org2_observation_id, evidence_hash=evidence_hash,
        )
        return request

    def submit_divergence_flag(self, request: DivergenceFlagRequest) -> TxReceipt:
        return self.record_receipt(
            "submit_divergence_flag",
            request,
            self.ledger.submit(Fn.CREATE_DIVERGENCE_FLAG, request=request.model_dump(mode="json")),
        )

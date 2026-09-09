"""Org2 Ingestion & Provenance Agent.

Org2 is both a producer of environmental data and a consumer of Org1 data
(Section 3). This agent handles the producer side: it is the only route by
which Org2's own sensor readings reach the ledger, mirroring Org1's ingestion
agent exactly, against Org2's own sensor network and plausibility ranges.

Deterministic throughout, for the same reason as Org1's: schema validation,
plausibility screening, hashing and provenance capture are not interpretive
work (Section 10).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_prototype.agents.base import Agent
from agent_prototype.ledger.port import Fn, TxReceipt
from agent_prototype.shared.models import (
    Calibration,
    ObservationAnchorRequest,
    ObservationRecord,
    Provenance,
    SensorRegistrationRequest,
)
from agent_prototype.shared.utilities.canonical import hash_payload

__all__ = [
    "IngestionProvenanceAgent",
    "PreparedAnchor",
    "ValidationFailure",
    "PLAUSIBLE_RANGES",
    "SCHEMA_VERSION",
]

SCHEMA_VERSION = "climate-airquality/1.0"

# Screening bounds for Org2's dense low-cost network. As with Org1, this is a
# sanity check on whether a reading is physically possible at all, not a
# quality assessment — quality scoring is QualityValidationAgent's job.
PLAUSIBLE_RANGES: dict[str, tuple[float, float]] = {
    "air_temperature": (-20.0, 60.0),
    "pm2_5": (0.0, 1000.0),
    "pm10": (0.0, 1500.0),
    "no2": (0.0, 2000.0),
    "o3": (0.0, 1000.0),
    "co": (0.0, 50000.0),
    "noise_db": (0.0, 140.0),
    "uv_index": (0.0, 20.0),
    "water_level": (-5.0, 15.0),
}


class ValidationFailure(Exception):
    """The raw record is malformed or physically implausible."""


@dataclass(frozen=True, slots=True)
class PreparedAnchor:
    """A transaction ready to propose, carried together with the off-chain
    record it anchors. Kept separate from Org1's `PreparedAnchor` so Org2's
    ingestion module has no dependency on Org1's internals."""

    request: ObservationAnchorRequest
    record: ObservationRecord
    data_hash: str


class IngestionProvenanceAgent(Agent):
    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_sensor(self, request: SensorRegistrationRequest) -> TxReceipt:
        return self.record_receipt(
            "register_sensor",
            request,
            self.ledger.submit(Fn.REGISTER_SENSOR, request=request.model_dump(mode="json")),
        )

    # ------------------------------------------------------------------
    # Observation ingestion
    # ------------------------------------------------------------------

    def prepare_anchor(
        self,
        observation_id: str,
        record: ObservationRecord,
        calibration: Calibration,
        *,
        uri: str | None = None,
        doi: str | None = None,
    ) -> PreparedAnchor:
        """Validate a raw sensor reading and build its anchor transaction.

        Pure: this touches no ledger. `record.station_id`/`record.instrument_id`
        both carry the sensor's id, since Org2 has one combined `Sensor` asset
        rather than Org1's separate station/instrument split.
        """
        self.validate(record)

        data_hash = hash_payload(record)
        request = ObservationAnchorRequest(
            observation_id=observation_id,
            station_id=record.station_id,
            instrument_id=record.instrument_id,
            phenomenon_time=record.phenomenon_time,
            data_hash=data_hash,
            calibration=calibration,
            provenance=Provenance(
                agent_id=self.agent_id,
                source_system=record.source_system,
                schema_version=SCHEMA_VERSION,
                ingested_at=self.clock.now(),
            ),
            uri=uri,
            doi=doi,
        )
        self.record(
            "prepare_anchor", record, "PREPARED",
            observation_id=observation_id, data_hash=data_hash,
        )
        return PreparedAnchor(request=request, record=record, data_hash=data_hash)

    def submit_anchor(self, prepared: PreparedAnchor) -> TxReceipt:
        """Propose the anchor. Whether it commits is the ledger's decision."""
        return self.record_receipt(
            "submit_anchor",
            prepared.request,
            self.ledger.submit(
                Fn.CREATE_OBSERVATION_ANCHOR, request=prepared.request.model_dump(mode="json")
            ),
        )

    # ------------------------------------------------------------------

    def validate(self, record: ObservationRecord) -> None:
        """Schema and range screening. Raises `ValidationFailure` if unusable."""
        unknown = sorted(set(record.variables) - set(PLAUSIBLE_RANGES))
        if unknown:
            raise ValidationFailure(
                f"unrecognised variables for an environmental record: {unknown}"
            )

        for name, variable in sorted(record.variables.items()):
            low, high = PLAUSIBLE_RANGES[name]
            if not low <= variable.value <= high:
                raise ValidationFailure(
                    f"{name}={variable.value}{variable.unit} is outside the plausible "
                    f"range [{low}, {high}]"
                )

        if record.phenomenon_time.tzinfo is None:
            raise ValidationFailure("phenomenon_time must be timezone-aware")

    def verify_anchor(self, observation_id: str, record: ObservationRecord) -> dict[str, Any]:
        """Re-derive the hash from raw data and check it against the ledger."""
        return self.ledger.evaluate(
            Fn.VERIFY_OBSERVATION_ANCHOR,
            owner_org=self.org_id,
            observation_id=observation_id,
            data_hash=hash_payload(record),
        )

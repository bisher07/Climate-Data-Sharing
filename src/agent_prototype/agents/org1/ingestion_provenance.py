"""Org1 Ingestion & Provenance Agent.

Org1 is the authoritative producer of meteorological observations and is close
to write-only with respect to them (Section 2). This agent is the only route by
which those observations reach the ledger.

Everything it does is deterministic — schema validation, plausibility
screening, content hashing, provenance capture, transaction preparation — which
is exactly the list Section 10 marks as *not* work for a language model. There
is no LLM here and there should not be one.

Responsibilities it deliberately does **not** hold: deciding whether Org1 is
willing to stand behind a record (the Policy/Endorsement Agent), and deciding
whether the record is admissible (the chaincode).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_prototype.agents.base import Agent
from agent_prototype.ledger.port import Fn, TxReceipt
from agent_prototype.shared.models import (
    Calibration,
    ForecastProductRequest,
    InstrumentRegistrationRequest,
    ObservationAnchorRequest,
    ObservationRecord,
    Provenance,
    StationRegistrationRequest,
)
from agent_prototype.shared.utilities.canonical import hash_payload

__all__ = [
    "IngestionProvenanceAgent",
    "PreparedAnchor",
    "ValidationFailure",
    "PLAUSIBLE_RANGES",
    "SCHEMA_VERSION",
]

SCHEMA_VERSION = "climate-observation/1.0"

# Physical plausibility bounds. This is a *screening* check for obviously broken
# input, not a quality assessment: quality scoring and anomaly detection belong
# to Org2's validation agent (Section 9). A failure here means "this is not a
# measurement at all", which is why it stops the record rather than annotating
# it with a low score.
PLAUSIBLE_RANGES: dict[str, tuple[float, float]] = {
    "air_temperature": (-90.0, 60.0),
    "relative_humidity": (0.0, 100.0),
    "surface_pressure": (800.0, 1100.0),
    "wind_speed": (0.0, 120.0),
    "wind_direction": (0.0, 360.0),
    "precipitation": (0.0, 2000.0),
}


class ValidationFailure(Exception):
    """The raw record is malformed or physically implausible."""


@dataclass(frozen=True, slots=True)
class PreparedAnchor:
    """A transaction ready to propose, carried together with the off-chain
    record it anchors.

    Keeping both in one object is what lets the Policy/Endorsement Agent — and
    later any auditor — re-derive `data_hash` from the raw bytes and confirm the
    anchor describes the data it claims to.
    """

    request: ObservationAnchorRequest
    record: ObservationRecord
    data_hash: str


class IngestionProvenanceAgent(Agent):
    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_station(self, request: StationRegistrationRequest) -> TxReceipt:
        return self.record_receipt(
            "register_station",
            request,
            self.ledger.submit(Fn.REGISTER_STATION,
                               request=request.model_dump(mode="json")),
        )

    def register_instrument(self, request: InstrumentRegistrationRequest) -> TxReceipt:
        return self.record_receipt(
            "register_instrument",
            request,
            self.ledger.submit(Fn.REGISTER_INSTRUMENT,
                               request=request.model_dump(mode="json")),
        )

    def register_forecast_product(self, request: ForecastProductRequest) -> TxReceipt:
        return self.record_receipt(
            "register_forecast_product",
            request,
            self.ledger.submit(
                Fn.REGISTER_FORECAST_PRODUCT, request=request.model_dump(
                    mode="json")
            ),
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
        """Validate a raw observation and build its anchor transaction.

        Pure: this touches no ledger, so the screening and provenance rules can
        be tested on their own. The raw record never leaves the agent — only its
        hash and provenance go on-chain (Section 8).
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
                Fn.CREATE_OBSERVATION_ANCHOR, request=prepared.request.model_dump(
                    mode="json")
            ),
        )

    # ------------------------------------------------------------------

    def validate(self, record: ObservationRecord) -> None:
        """Schema and range screening. Raises `ValidationFailure` if unusable."""
        unknown = sorted(set(record.variables) - set(PLAUSIBLE_RANGES))
        if unknown:
            raise ValidationFailure(
                f"unrecognised variables for a meteorological record: {unknown}"
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

"""Org1 domain assets.

Two families of model live here and the split is deliberate:

* `*Request` models are what an agent proposes. They carry no ownership field.
* Ledger assets are what chaincode stores. They carry `owner_org`, which the
  chaincode fills in from the submitting certificate's MSP ID.

A client therefore has no way to *state* who owns an asset it is writing, which
is what makes "Org3 cannot pretend to own Org1 data" (Section 12) a structural
property rather than a validation rule someone can forget to apply.
"""

from __future__ import annotations
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from pydantic import BaseModel, ConfigDict, Field

Latitude = Annotated[float, Field(ge=-90.0, le=90.0)]
Longitude = Annotated[float, Field(ge=-180.0, le=180.0)]
NonEmpty = Annotated[str, Field(min_length=1, max_length=256)]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class DocType(StrEnum):
    """Discriminator stored on every ledger asset, for range queries."""

    STATION = "station"
    INSTRUMENT = "instrument"
    OBSERVATION_ANCHOR = "observation_anchor"
    FORECAST_PRODUCT = "forecast_product"
    ACCESS_REQUEST = "access_request"
    ACCESS_DECISION = "access_decision"


class AssetStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DECOMMISSIONED = "DECOMMISSIONED"


class QualityStatus(StrEnum):
    """Set to UNVALIDATED by Org1 and never changed by Org1.

    Org2 owns validation (Section 9) but cannot write into Org1's namespace, so
    it records quality as a separate Org2-owned QualityRecord that references
    this anchor. This field exists so an auditor can see that Org1 makes no
    quality claim about its own raw data.
    """

    UNVALIDATED = "UNVALIDATED"


class AccessDecisionStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Provenance(Frozen):
    """Who produced this record, how, and from where (Section 2)."""

    agent_id: NonEmpty
    source_system: NonEmpty
    schema_version: NonEmpty
    ingested_at: datetime
    method: NonEmpty = "direct-ingest"


class Calibration(Frozen):
    """Instrument calibration metadata anchored alongside observations."""

    calibrated_at: datetime
    calibrated_by: NonEmpty
    procedure: NonEmpty
    uncertainty: float = Field(ge=0.0)
    unit: NonEmpty


# --------------------------------------------------------------------------
# Off-chain payload
# --------------------------------------------------------------------------


class Variable(Frozen):
    value: float
    unit: NonEmpty


class ObservationRecord(Frozen):
    """The raw meteorological reading. This stays OFF-chain (Section 8).

    Only its canonical hash is anchored, so the ledger carries integrity and
    provenance without carrying bulk climate data.
    """

    station_id: NonEmpty
    instrument_id: NonEmpty
    phenomenon_time: datetime
    variables: dict[str, Variable] = Field(min_length=1)
    source_system: NonEmpty


# --------------------------------------------------------------------------
# Proposal inputs (no owner_org by construction)
# --------------------------------------------------------------------------


class StationRegistrationRequest(Frozen):
    station_id: NonEmpty
    name: NonEmpty
    latitude: Latitude
    longitude: Longitude
    elevation_m: float
    wmo_id: str | None = None


class InstrumentRegistrationRequest(Frozen):
    instrument_id: NonEmpty
    station_id: NonEmpty
    kind: NonEmpty
    model: NonEmpty
    serial_number: NonEmpty
    calibration: Calibration


class ObservationAnchorRequest(Frozen):
    observation_id: NonEmpty
    station_id: NonEmpty
    instrument_id: NonEmpty
    phenomenon_time: datetime
    data_hash: Sha256Hex
    calibration: Calibration
    provenance: Provenance
    uri: str | None = None
    doi: str | None = None


class ForecastProductRequest(Frozen):
    product_id: NonEmpty
    product_type: NonEmpty
    issued_at: datetime
    valid_from: datetime
    valid_to: datetime
    horizon_hours: int = Field(ge=0)
    data_hash: Sha256Hex
    provenance: Provenance
    uri: str | None = None
    doi: str | None = None
    embargoed_until: datetime | None = None


# --------------------------------------------------------------------------
# Ledger assets (owner_org assigned by chaincode)
# --------------------------------------------------------------------------


class LedgerAsset(Frozen):
    doc_type: DocType
    id: NonEmpty
    owner_org: NonEmpty
    created_at: datetime
    created_by_tx: NonEmpty


class Station(LedgerAsset):
    doc_type: DocType = DocType.STATION
    name: NonEmpty
    latitude: Latitude
    longitude: Longitude
    elevation_m: float
    wmo_id: str | None = None
    status: AssetStatus = AssetStatus.ACTIVE


class Instrument(LedgerAsset):
    doc_type: DocType = DocType.INSTRUMENT
    station_id: NonEmpty
    kind: NonEmpty
    model: NonEmpty
    serial_number: NonEmpty
    calibration: Calibration
    status: AssetStatus = AssetStatus.ACTIVE


class ObservationAnchor(LedgerAsset):
    doc_type: DocType = DocType.OBSERVATION_ANCHOR
    station_id: NonEmpty
    instrument_id: NonEmpty
    phenomenon_time: datetime
    data_hash: Sha256Hex
    calibration: Calibration
    provenance: Provenance
    uri: str | None = None
    doi: str | None = None
    quality_status: QualityStatus = QualityStatus.UNVALIDATED


class ForecastProduct(LedgerAsset):
    doc_type: DocType = DocType.FORECAST_PRODUCT
    product_type: NonEmpty
    issued_at: datetime
    valid_from: datetime
    valid_to: datetime
    horizon_hours: int = Field(ge=0)
    data_hash: Sha256Hex
    provenance: Provenance
    uri: str | None = None
    doi: str | None = None
    embargoed_until: datetime | None = None


# --------------------------------------------------------------------------
# Access negotiation
#
# An access request is owned by the organization that *asks* (Org3), and the
# answer is a separate asset owned by the organization that *decides* (Org1).
# They cannot be one mutable record, because no organization may write into
# another's namespace (Section 12).
# --------------------------------------------------------------------------


class AssetRef(Frozen):
    """A pointer to an asset in some organization's namespace."""

    owner_org: NonEmpty
    doc_type: DocType
    asset_id: NonEmpty

    def __str__(self) -> str:
        return f"{self.owner_org}/{self.doc_type}/{self.asset_id}"


class AccessRequestSubmission(Frozen):
    """What a requesting organization proposes. Carries no requester field:
    the requester is whoever's identity submits it."""

    request_id: NonEmpty
    target_org: NonEmpty
    purpose: NonEmpty
    justification: NonEmpty
    requested: list[AssetRef] = Field(min_length=1)
    valid_until: datetime | None = None


class AccessRequest(LedgerAsset):
    """Written by the requesting organization. Org1 only ever reads these."""

    doc_type: DocType = DocType.ACCESS_REQUEST
    target_org: NonEmpty
    purpose: NonEmpty
    justification: NonEmpty
    requested: list[AssetRef] = Field(min_length=1)
    valid_until: datetime | None = None

    @property
    def requester_org(self) -> str:
        """Who is asking. This is the record's owner by construction: the
        request lives in the requesting organization's namespace, so there is
        no separate field that could disagree with it."""
        return self.owner_org


class AccessDecisionRequest(Frozen):
    """What Org1's policy agent proposes in answer to a request."""

    decision_id: NonEmpty
    request_id: NonEmpty
    requester_org: NonEmpty
    approved: bool
    reason: NonEmpty
    granted: list[AssetRef] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    valid_until: datetime | None = None


class AccessDecision(LedgerAsset):
    """Written by the deciding organization, in its own namespace."""

    doc_type: DocType = DocType.ACCESS_DECISION
    request_id: NonEmpty
    requester_org: NonEmpty
    approved: bool
    reason: NonEmpty
    granted: list[AssetRef] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    valid_until: datetime | None = None


def as_state(asset: LedgerAsset) -> dict[str, Any]:
    return asset.model_dump(mode="json")

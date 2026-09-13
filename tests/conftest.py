"""Fixtures: Org1's and Org2's agents wired to one in-memory ledger.

The ledger here is a stand-in with no security properties. These tests are
about agent *behaviour* — what it screens, what it refuses, what it records —
not about whether the blockchain enforces anything.

Org2's connection is made with `connect_as`, so both organizations' agents see
the same state. That is what makes cross-org divergence testable: Org2 has to
be able to read an Org1 anchor to disagree with it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_prototype.agents import org2 as org2_agents
from agent_prototype.agents.org1 import (
    IngestionProvenanceAgent,
    Org1IngestionPipeline,
    PolicyEndorsementAgent,
)
from agent_prototype.ledger import InMemoryLedger
from agent_prototype.shared.models import (
    AssetRef,
    Calibration,
    DocType,
    InstrumentRegistrationRequest,
    ObservationRecord,
    SensorRegistrationRequest,
    StationRegistrationRequest,
    Variable,
)
from agent_prototype.shared.utilities import FixedClock

ORG1 = "Org1MSP"
ORG2 = "Org2MSP"
ORG3 = "Org3MSP"
T0 = datetime(2026, 1, 15, tzinfo=UTC)
PHENOMENON_TIME = datetime(2026, 1, 14, 12, 0, tzinfo=UTC)
SENSOR_ID = "SN-AQ-077"


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(T0, step=timedelta(seconds=1))


@pytest.fixture
def ledger(clock) -> InMemoryLedger:
    return InMemoryLedger(ORG1, clock=clock)


@pytest.fixture
def ingestion(ledger, clock) -> IngestionProvenanceAgent:
    return IngestionProvenanceAgent("org1.ingestion", ledger, clock=clock)


@pytest.fixture
def policy(ledger, clock) -> PolicyEndorsementAgent:
    return PolicyEndorsementAgent("org1.policy", ledger, clock=clock)


@pytest.fixture
def pipeline(ingestion, policy) -> Org1IngestionPipeline:
    return Org1IngestionPipeline(ingestion, policy)


@pytest.fixture
def calibration() -> Calibration:
    return Calibration(
        calibrated_at=datetime(2025, 12, 1, tzinfo=UTC),
        calibrated_by="NMI-UAE",
        procedure="ISO-17025",
        uncertainty=0.15,
        unit="degC",
    )


@pytest.fixture
def station_request() -> StationRegistrationRequest:
    return StationRegistrationRequest(
        station_id="ST-SHJ-001",
        name="Sharjah Coastal AWS",
        latitude=25.3463,
        longitude=55.4209,
        elevation_m=8.0,
        wmo_id="41556",
    )


@pytest.fixture
def instrument_request(calibration) -> InstrumentRegistrationRequest:
    return InstrumentRegistrationRequest(
        instrument_id="IN-THERM-014",
        station_id="ST-SHJ-001",
        kind="thermometer",
        model="Vaisala HMP155",
        serial_number="J1940123",
        calibration=calibration,
    )


@pytest.fixture
def observation() -> ObservationRecord:
    return ObservationRecord(
        station_id="ST-SHJ-001",
        instrument_id="IN-THERM-014",
        phenomenon_time=PHENOMENON_TIME,
        variables={
            "air_temperature": Variable(value=27.4, unit="degC"),
            "relative_humidity": Variable(value=62.0, unit="percent"),
        },
        source_system="org1-aws-telemetry",
    )


@pytest.fixture
def registered(ingestion, station_request, instrument_request):
    """Org1 with its station and instrument already on the ledger."""
    assert ingestion.register_station(station_request).committed
    assert ingestion.register_instrument(instrument_request).committed
    return ingestion


# --- Org2 -------------------------------------------------------------------


@pytest.fixture
def org2_ledger(ledger) -> InMemoryLedger:
    """Org2's view of the same ledger Org1 is writing to."""
    return ledger.connect_as(ORG2)


@pytest.fixture
def org2_ingestion(org2_ledger, clock) -> org2_agents.IngestionProvenanceAgent:
    return org2_agents.IngestionProvenanceAgent("org2.ingestion", org2_ledger, clock=clock)


@pytest.fixture
def org2_quality(org2_ledger, clock) -> org2_agents.QualityValidationAgent:
    return org2_agents.QualityValidationAgent("org2.quality", org2_ledger, clock=clock)


@pytest.fixture
def org2_policy(org2_ledger, clock) -> org2_agents.PolicyEndorsementAgent:
    return org2_agents.PolicyEndorsementAgent("org2.policy", org2_ledger, clock=clock)


@pytest.fixture
def org2_negotiation(org2_ledger, clock) -> org2_agents.AccessNegotiationAgent:
    return org2_agents.AccessNegotiationAgent("org2.negotiation", org2_ledger, clock=clock)


@pytest.fixture
def org2_pipeline(
    org2_ingestion, org2_quality, org2_policy, org2_negotiation
) -> org2_agents.Org2ValidationPipeline:
    return org2_agents.Org2ValidationPipeline(
        org2_ingestion, org2_quality, org2_policy, org2_negotiation
    )


ORG1_STATION = AssetRef(owner_org=ORG1, doc_type=DocType.STATION, asset_id="ST-SHJ-001")


@pytest.fixture
def org2_granted(org2_negotiation, policy, registered):
    """Org2 holding Org1's approval for station ST-SHJ-001, and so for the
    readings that station produces."""
    submission = org2_negotiation.draft_request(
        "AR-ORG2-1",
        target_org=ORG1,
        purpose="validation",
        justification="Cross-checking Org2 microclimate nodes against the coastal AWS.",
        requested=[ORG1_STATION],
    )
    assert org2_negotiation.submit_request(submission).committed
    request = policy.pending_access_requests()[0]
    assert policy.respond_to_access_request(
        request, policy.review_access_request(request)
    ).committed
    return org2_negotiation


@pytest.fixture
def sensor_request(calibration) -> SensorRegistrationRequest:
    return SensorRegistrationRequest(
        sensor_id=SENSOR_ID,
        name="Al Majaz microclimate node",
        latitude=25.3241,
        longitude=55.3869,
        elevation_m=5.0,
        kind="microclimate",
        model="Clarity Node-S",
        serial_number="CN-88213",
        calibration=calibration,
    )


@pytest.fixture
def org2_observation() -> ObservationRecord:
    """Both id fields carry the sensor id: Org2 has one combined Sensor asset
    rather than Org1's separate station and instrument."""
    return ObservationRecord(
        station_id=SENSOR_ID,
        instrument_id=SENSOR_ID,
        phenomenon_time=PHENOMENON_TIME,
        variables={
            "air_temperature": Variable(value=27.9, unit="degC"),
            "pm2_5": Variable(value=18.0, unit="ug/m3"),
        },
        source_system="org2-sensor-gateway",
    )


@pytest.fixture
def org2_registered(org2_ingestion, sensor_request):
    """Org2 with its sensor already on the ledger."""
    assert org2_ingestion.register_sensor(sensor_request).committed
    return org2_ingestion

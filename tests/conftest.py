"""Fixtures: Org1's agents wired to an in-memory ledger.

The ledger here is a stand-in with no security properties. These tests are
about agent *behaviour* — what it screens, what it refuses, what it records —
not about whether the blockchain enforces anything.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agent_prototype.agents.org1 import (
    IngestionProvenanceAgent,
    Org1IngestionPipeline,
    PolicyEndorsementAgent,
)
from agent_prototype.ledger import InMemoryLedger
from agent_prototype.shared.models import (
    Calibration,
    InstrumentRegistrationRequest,
    ObservationRecord,
    StationRegistrationRequest,
    Variable,
)
from agent_prototype.shared.utilities import FixedClock

ORG1 = "Org1MSP"
ORG3 = "Org3MSP"
T0 = datetime(2026, 1, 15, tzinfo=UTC)
PHENOMENON_TIME = datetime(2026, 1, 14, 12, 0, tzinfo=UTC)


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

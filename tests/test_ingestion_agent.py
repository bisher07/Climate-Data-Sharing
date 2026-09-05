"""Org1 Ingestion & Provenance Agent: screening, hashing, provenance."""

from __future__ import annotations

from datetime import datetime

import pytest

from agent_prototype.agents.org1 import SCHEMA_VERSION, ValidationFailure
from agent_prototype.shared.models import ObservationRecord, Variable
from agent_prototype.shared.utilities import hash_payload


# --- screening is pure and needs no ledger ----------------------------------


def test_a_plausible_record_passes_screening(ingestion, observation):
    ingestion.validate(observation)  # does not raise


def test_an_impossible_temperature_is_rejected(ingestion, observation):
    absurd = observation.model_copy(
        update={"variables": {"air_temperature": Variable(value=412.0, unit="degC")}}
    )

    with pytest.raises(ValidationFailure, match=r"outside the plausible range"):
        ingestion.validate(absurd)


def test_an_unrecognised_variable_is_rejected(ingestion, observation):
    """Org1 anchors meteorological variables. Anything else is a schema error,
    not a low-quality reading."""
    odd = observation.model_copy(
        update={"variables": {"pm2_5": Variable(value=12.0, unit="ug/m3")}}
    )

    with pytest.raises(ValidationFailure, match=r"unrecognised variables.*pm2_5"):
        ingestion.validate(odd)


def test_a_naive_timestamp_is_rejected(ingestion):
    """Without a timezone the observation cannot be ordered against another
    organization's data, which is the whole point of anchoring it."""
    naive = ObservationRecord.model_construct(
        station_id="ST-SHJ-001",
        instrument_id="IN-THERM-014",
        phenomenon_time=datetime(2026, 1, 14, 12, 0),
        variables={"air_temperature": Variable(value=27.4, unit="degC")},
        source_system="org1-aws-telemetry",
    )

    with pytest.raises(ValidationFailure, match="timezone-aware"):
        ingestion.validate(naive)


# --- hashing and provenance --------------------------------------------------


def test_the_anchor_hashes_the_record_it_carries(ingestion, observation, calibration):
    prepared = ingestion.prepare_anchor("OBS-1", observation, calibration)

    assert prepared.data_hash == hash_payload(observation)
    assert prepared.request.data_hash == prepared.data_hash


def test_hashing_is_stable_across_key_order(ingestion, observation, calibration):
    """Two organizations must derive the same digest from the same reading, or
    lineage and divergence detection cannot work."""
    reordered = observation.model_copy(
        update={"variables": dict(reversed(list(observation.variables.items())))}
    )

    assert hash_payload(reordered) == hash_payload(observation)


def test_a_changed_reading_changes_the_hash(ingestion, observation, calibration):
    prepared = ingestion.prepare_anchor("OBS-1", observation, calibration)
    tampered = observation.model_copy(
        update={"variables": {**observation.variables,
                              "air_temperature": Variable(value=27.5, unit="degC")}}
    )

    assert hash_payload(tampered) != prepared.data_hash


def test_provenance_is_captured_from_the_agent_not_the_payload(
    ingestion, observation, calibration
):
    prepared = ingestion.prepare_anchor("OBS-1", observation, calibration)
    provenance = prepared.request.provenance

    assert provenance.agent_id == "org1.ingestion"
    assert provenance.source_system == "org1-aws-telemetry"
    assert provenance.schema_version == SCHEMA_VERSION
    assert provenance.ingested_at.tzinfo is not None


def test_preparing_an_anchor_touches_no_ledger(ingestion, observation, calibration):
    """Screening and provenance are pure, so they are testable and replayable
    without a network."""
    ingestion.prepare_anchor("OBS-1", observation, calibration)

    assert ingestion.ledger.submitted == []


# --- submission --------------------------------------------------------------


def test_an_anchor_carries_only_the_hash_to_the_ledger(
    ingestion, registered, observation, calibration
):
    """Section 8: the raw dataset stays off-chain."""
    prepared = ingestion.prepare_anchor("OBS-1", observation, calibration)
    ingestion.submit_anchor(prepared)

    _, arguments = ingestion.ledger.submitted[-1]
    assert arguments["request"]["data_hash"] == prepared.data_hash
    assert "variables" not in arguments["request"]
    assert "27.4" not in repr(arguments)


def test_verification_matches_the_original_and_not_a_tampered_copy(
    ingestion, registered, observation, calibration
):
    ingestion.submit_anchor(ingestion.prepare_anchor("OBS-1", observation, calibration))
    tampered = observation.model_copy(
        update={"variables": {**observation.variables,
                              "air_temperature": Variable(value=21.0, unit="degC")}}
    )

    assert ingestion.verify_anchor("OBS-1", observation)["matches"] is True
    assert ingestion.verify_anchor("OBS-1", tampered)["matches"] is False


def test_a_ledger_refusal_is_reported_not_raised(ingestion, observation, calibration):
    """With no station registered the ledger refuses. The agent must surface
    that as an outcome rather than treating it as a crash."""
    prepared = ingestion.prepare_anchor("OBS-1", observation, calibration)

    receipt = ingestion.submit_anchor(prepared)
    assert not receipt.committed
    assert "station" in receipt.message

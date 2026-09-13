"""Org2's validation workflow — Scenarios B and C of Section 6."""

from __future__ import annotations

from agent_prototype.agents.org2 import Stage
from agent_prototype.shared.models import Variable

from tests.conftest import ORG1


def _anchors(ledger) -> list:
    return [fn for fn, _ in ledger.submitted if fn == "CreateObservationAnchor"]


# --- Scenario B: Org2 observation → quality score → ledger ------------------


def test_a_good_reading_is_anchored_and_scored(
    org2_pipeline, org2_registered, org2_observation, calibration
):
    result = org2_pipeline.ingest("OBS-1", org2_observation, calibration)

    assert result.stage is Stage.COMMITTED
    assert result.receipt.committed
    assert result.quality_receipt.committed
    assert result.quality.observation_id == "OBS-1"


def test_an_impossible_reading_stops_at_screening(
    org2_pipeline, org2_registered, org2_observation, calibration, org2_ledger
):
    absurd = org2_observation.model_copy(
        update={"variables": {"pm2_5": Variable(value=9_000.0, unit="ug/m3")}}
    )

    result = org2_pipeline.ingest("OBS-BAD", absurd, calibration)

    assert result.stage is Stage.SCREENING
    assert "outside the plausible range" in result.error
    assert not _anchors(org2_ledger)


def test_an_unregistered_sensor_stops_at_policy_review(
    org2_pipeline, org2_observation, calibration, org2_ledger
):
    result = org2_pipeline.ingest("OBS-1", org2_observation, calibration)

    assert result.stage is Stage.POLICY_REVIEW
    assert "not registered" in result.error
    assert not _anchors(org2_ledger)


def test_a_low_score_is_published_rather_than_suppressed(
    org2_pipeline, org2_registered, org2_observation, calibration
):
    """Section 9: make doubt visible rather than suppress the record it
    attaches to. A poor score rides along with the anchor; it does not gate it."""
    extreme = org2_observation.model_copy(
        update={"variables": {"air_temperature": Variable(value=58.0, unit="degC")}}
    )

    result = org2_pipeline.ingest("OBS-EXTREME", extreme, calibration)

    assert result.stage is Stage.COMMITTED
    assert result.quality.quality_score < 0.5
    assert result.quality_receipt.committed


def test_the_raw_reading_never_reaches_the_ledger(
    org2_pipeline, org2_registered, org2_observation, calibration, org2_ledger
):
    """Section 8: only the hash and provenance are anchored."""
    org2_pipeline.ingest("OBS-1", org2_observation, calibration)

    submitted = repr(org2_ledger.submitted)
    assert "27.9" not in submitted
    assert "ug/m3" not in submitted


# --- Scenario C: Org1 + Org2 observations → divergence flag -----------------


def _both_anchored(org2_pipeline, ingestion, org2_observation, observation, calibration):
    assert org2_pipeline.ingest("OBS-ORG2", org2_observation, calibration).committed
    assert ingestion.submit_anchor(
        ingestion.prepare_anchor("OBS-ORG1", observation, calibration)
    ).committed


def test_a_reading_org2_was_never_granted_is_not_compared(
    org2_pipeline, org2_quality, org2_registered, registered, ingestion,
    org2_observation, observation, calibration, org2_ledger,
):
    """Holding another organization's raw reading is the use that needs a
    grant, so without one the comparison is never computed at all."""
    _both_anchored(org2_pipeline, ingestion, org2_observation, observation, calibration)
    before = len(org2_ledger.submitted)

    result = org2_pipeline.compare(
        "OBS-ORG2", org2_observation,
        reference_org=ORG1, reference_observation_id="OBS-ORG1",
        reference_record=observation, variable="air_temperature", threshold=2.0,
    )

    assert result.stage is Stage.NOT_GRANTED
    assert "has not released" in result.error
    assert len(org2_ledger.submitted) == before
    assert not any(e.action == "detect_divergence" for e in org2_quality.audit_trail)


def test_agreeing_readings_produce_no_flag(
    org2_pipeline, org2_registered, org2_granted, ingestion,
    org2_observation, observation, calibration, org2_ledger,
):
    _both_anchored(org2_pipeline, ingestion, org2_observation, observation, calibration)
    before = len(org2_ledger.submitted)

    result = org2_pipeline.compare(
        "OBS-ORG2", org2_observation,
        reference_org=ORG1, reference_observation_id="OBS-ORG1",
        reference_record=observation, variable="air_temperature", threshold=2.0,
    )

    assert result.stage is Stage.WITHIN_TOLERANCE
    assert len(org2_ledger.submitted) == before


def test_disagreeing_readings_are_flagged_on_the_ledger(
    org2_pipeline, org2_registered, org2_granted, ingestion,
    org2_observation, observation, calibration,
):
    cold = observation.model_copy(
        update={"variables": {**observation.variables,
                              "air_temperature": Variable(value=19.0, unit="degC")}}
    )
    _both_anchored(org2_pipeline, ingestion, org2_observation, cold, calibration)

    result = org2_pipeline.compare(
        "OBS-ORG2", org2_observation,
        reference_org=ORG1, reference_observation_id="OBS-ORG1",
        reference_record=cold, variable="air_temperature", threshold=2.0,
    )

    assert result.stage is Stage.COMMITTED
    assert result.receipt.payload["reference_org"] == ORG1
    assert result.divergence.divergence_metric > 2.0


def test_the_flag_is_owned_by_org2_not_by_the_org_it_disagrees_with(
    org2_pipeline, org2_registered, org2_granted, ingestion,
    org2_observation, observation, calibration,
):
    """Section 9: Org2 writes the flag, in Org2's namespace. Disagreeing with
    Org1 does not let Org2 write anything into Org1's."""
    cold = observation.model_copy(
        update={"variables": {**observation.variables,
                              "air_temperature": Variable(value=19.0, unit="degC")}}
    )
    _both_anchored(org2_pipeline, ingestion, org2_observation, cold, calibration)

    result = org2_pipeline.compare(
        "OBS-ORG2", org2_observation,
        reference_org=ORG1, reference_observation_id="OBS-ORG1",
        reference_record=cold, variable="air_temperature", threshold=2.0,
    )

    assert result.receipt.payload["owner_org"] == "Org2MSP"


def test_a_pipeline_cannot_be_built_from_two_organizations_agents(
    org2_ingestion, org2_quality, policy, org2_negotiation
):
    import pytest

    from agent_prototype.agents.org2 import Org2ValidationPipeline

    with pytest.raises(ValueError, match="different organizations"):
        Org2ValidationPipeline(org2_ingestion, org2_quality, policy, org2_negotiation)

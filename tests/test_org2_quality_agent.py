"""Org2 Quality / Validation Agent: scoring, and making disagreement visible."""

from __future__ import annotations

from datetime import timedelta

from agent_prototype.agents.org2 import QualityValidationAgent
from agent_prototype.shared.models import Variable

from tests.conftest import ORG1, SENSOR_ID


# --- scoring Org2's own reading ---------------------------------------------


def test_a_mid_range_reading_scores_higher_than_an_extreme_one(
    org2_quality, org2_observation, calibration
):
    """The score is about the reading, not about whether it is admissible:
    screening has already removed anything physically impossible."""
    mid = org2_quality.assess_quality("OBS-1", org2_observation, calibration)
    extreme = org2_quality.assess_quality(
        "OBS-2",
        org2_observation.model_copy(
            update={"variables": {"air_temperature": Variable(value=59.0, unit="degC")}}
        ),
        calibration,
    )

    assert mid.quality_score > extreme.quality_score


def test_fresh_calibration_gives_full_confidence(org2_quality, org2_observation, calibration):
    scored = org2_quality.assess_quality("OBS-1", org2_observation, calibration)

    assert scored.confidence == 1.0


def test_confidence_decays_once_calibration_is_overdue(
    org2_ledger, clock, org2_observation, calibration
):
    """Confidence is about the measurement pipeline, not the reading: a stale
    calibration makes any value less trustworthy, however plausible it looks."""
    strict = QualityValidationAgent(
        "org2.quality", org2_ledger, clock=clock, max_calibration_age=timedelta(days=7)
    )

    scored = strict.assess_quality("OBS-1", org2_observation, calibration)
    assert 0.0 <= scored.confidence < 1.0


def test_a_quality_record_names_the_agent_that_made_it(
    org2_quality, org2_observation, calibration
):
    scored = org2_quality.assess_quality("OBS-1", org2_observation, calibration)

    assert scored.agent_id == "org2.quality"
    assert scored.observation_id == "OBS-1"


# --- disagreeing with another organization ----------------------------------


def _org1_reading(observation, value: float):
    return observation.model_copy(
        update={"variables": {**observation.variables,
                              "air_temperature": Variable(value=value, unit="degC")}}
    )


def test_readings_that_agree_are_not_flagged(org2_quality, org2_observation, observation):
    """27.9 against 27.4 is half a degree apart, inside a 2.0 tolerance."""
    flag = org2_quality.detect_divergence(
        "OBS-ORG2", org2_observation, ORG1, "OBS-ORG1", observation,
        variable="air_temperature", threshold=2.0,
    )

    assert flag is None
    assert org2_quality.audit_trail[-1].outcome == "WITHIN_TOLERANCE"


def test_readings_that_disagree_produce_a_flag(org2_quality, org2_observation, observation):
    flag = org2_quality.detect_divergence(
        "OBS-ORG2", org2_observation, ORG1, "OBS-ORG1", _org1_reading(observation, 19.0),
        variable="air_temperature", threshold=2.0,
    )

    assert flag is not None
    assert flag.reference_org == ORG1
    assert flag.divergence_metric > flag.threshold


def test_a_flag_records_evidence_rather_than_a_verdict(
    org2_quality, org2_observation, observation
):
    """Section 9: Org2 is not a neutral third party, so the flag says the two
    readings differ and by how much. It does not say Org1 is wrong."""
    flag = org2_quality.detect_divergence(
        "OBS-ORG2", org2_observation, ORG1, "OBS-ORG1", _org1_reading(observation, 19.0),
        variable="air_temperature", threshold=2.0,
    )

    fields = set(flag.model_dump())
    assert "evidence_hash" in fields
    assert not {"verdict", "correct_org", "at_fault"} & fields


def test_a_variable_only_one_side_measured_is_skipped(
    org2_quality, org2_observation, observation
):
    """Org1 does not measure pm2_5, so there is nothing to disagree about."""
    flag = org2_quality.detect_divergence(
        "OBS-ORG2", org2_observation, ORG1, "OBS-ORG1", observation,
        variable="pm2_5", threshold=1.0,
    )

    assert flag is None
    assert org2_quality.audit_trail[-1].outcome == "SKIPPED"


def test_the_evidence_hash_covers_both_readings(org2_quality, org2_observation, observation):
    """Two comparisons that differ only in Org1's value must not produce the
    same evidence hash, or the evidence would not pin down what was compared."""
    first = org2_quality.detect_divergence(
        "OBS-ORG2", org2_observation, ORG1, "OBS-ORG1", _org1_reading(observation, 19.0),
        variable="air_temperature", threshold=2.0,
    )
    second = org2_quality.detect_divergence(
        "OBS-ORG2", org2_observation, ORG1, "OBS-ORG1", _org1_reading(observation, 18.0),
        variable="air_temperature", threshold=2.0,
    )

    assert first.evidence_hash != second.evidence_hash


def test_a_flag_cannot_be_written_against_an_observation_that_does_not_exist(
    org2_quality, org2_registered, org2_observation, observation
):
    """The stand-in ledger refuses it, and the agent reports the refusal rather
    than raising: a rejection is a normal, countable outcome."""
    flag = org2_quality.detect_divergence(
        "OBS-NEVER-ANCHORED", org2_observation, ORG1, "OBS-ORG1",
        _org1_reading(observation, 19.0), variable="air_temperature", threshold=2.0,
    )

    receipt = org2_quality.submit_divergence_flag(flag)
    assert not receipt.committed
    assert SENSOR_ID not in (receipt.message or "")

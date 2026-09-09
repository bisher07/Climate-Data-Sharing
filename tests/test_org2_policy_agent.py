"""Org2 Policy / Endorsement Agent: what Org2 will and will not stand behind."""

from __future__ import annotations

from datetime import timedelta

from agent_prototype.agents.org2 import PolicyEndorsementAgent
from agent_prototype.ledger import Fn
from agent_prototype.shared.models import (
    AccessRequest,
    AccessRequestSubmission,
    AssetRef,
    DivergenceFlagRequest,
    DocType,
    Variable,
)

from tests.conftest import ORG1, ORG2, ORG3, SENSOR_ID, T0


# --- Org2's own observations ------------------------------------------------


def test_a_well_formed_anchor_is_approved(
    org2_ingestion, org2_policy, org2_registered, org2_observation, calibration
):
    prepared = org2_ingestion.prepare_anchor("OBS-1", org2_observation, calibration)

    decision = org2_policy.review_anchor(prepared)
    assert decision.approved
    assert decision.concerns == ()


def test_an_anchor_for_an_unregistered_sensor_is_refused(
    org2_ingestion, org2_policy, org2_observation, calibration
):
    prepared = org2_ingestion.prepare_anchor("OBS-1", org2_observation, calibration)

    decision = org2_policy.review_anchor(prepared)
    assert not decision.approved
    assert f"sensor '{SENSOR_ID}' is not registered by {ORG2}" in decision.reasons[0]


def test_an_anchor_that_misdescribes_its_record_is_refused(
    org2_ingestion, org2_policy, org2_registered, org2_observation, calibration
):
    from agent_prototype.agents.org2 import PreparedAnchor

    prepared = org2_ingestion.prepare_anchor("OBS-1", org2_observation, calibration)
    swapped = PreparedAnchor(
        request=prepared.request,
        record=org2_observation.model_copy(
            update={"variables": {**org2_observation.variables,
                                  "pm2_5": Variable(value=400.0, unit="ug/m3")}}
        ),
        data_hash=prepared.data_hash,
    )

    decision = org2_policy.review_anchor(swapped)
    assert not decision.approved
    assert "does not match the raw record" in decision.reasons[0]


def test_an_anchor_naming_two_different_devices_is_refused(
    org2_ingestion, org2_policy, org2_registered, org2_observation, calibration
):
    """Org2 registers one combined Sensor per site, so an anchor whose station
    and instrument ids disagree describes a pairing Org2 does not have."""
    prepared = org2_ingestion.prepare_anchor(
        "OBS-1",
        org2_observation.model_copy(update={"instrument_id": "SN-SOMETHING-ELSE"}),
        calibration,
    )

    decision = org2_policy.review_anchor(prepared)
    assert not decision.approved
    assert "one sensor per site" in decision.reasons[0]


def test_stale_calibration_is_surfaced_but_does_not_block(
    org2_ingestion, org2_ledger, org2_registered, org2_observation, calibration, clock
):
    strict = PolicyEndorsementAgent(
        "org2.policy", org2_ledger, clock=clock, max_calibration_age=timedelta(days=1)
    )
    prepared = org2_ingestion.prepare_anchor("OBS-1", org2_observation, calibration)

    decision = strict.review_anchor(prepared)
    assert decision.approved
    assert "calibration is" in decision.concerns[0]


def test_org2_never_approves_another_organizations_data(org2_policy):
    """Section 3: Org2 cannot approve Org1-owned raw data."""
    decision = org2_policy.review_foreign_data(ORG1, "an Org1 temperature observation")

    assert not decision.approved
    assert "has no standing to approve it" in decision.reasons[0]
    assert org2_policy.review_foreign_data(ORG2, "an Org2 sensor reading").approved


# --- quality records --------------------------------------------------------


def test_a_quality_record_about_org2s_own_reading_is_approved(
    org2_pipeline, org2_policy, org2_quality, org2_registered, org2_observation, calibration
):
    org2_pipeline.ingest("OBS-1", org2_observation, calibration)
    scored = org2_quality.assess_quality("OBS-1", org2_observation, calibration)

    assert org2_policy.review_quality_record(scored).approved


def test_org2_will_not_score_another_organizations_reading(
    org2_policy, org2_quality, registered, observation, calibration, ingestion
):
    """Judging Org1's raw data is what a divergence flag is for. A quality
    record is a claim about a reading, so Org2 makes them only about its own."""
    prepared = ingestion.prepare_anchor("OBS-ORG1", observation, calibration)
    assert ingestion.submit_anchor(prepared).committed

    scored = org2_quality.assess_quality("OBS-ORG1", observation, calibration)
    decision = org2_policy.review_quality_record(scored)

    assert not decision.approved
    assert "only for its own readings" in decision.reasons[0]


def test_low_confidence_is_a_concern_not_a_refusal(
    org2_pipeline, org2_policy, org2_quality, org2_registered, org2_observation, calibration
):
    org2_pipeline.ingest("OBS-1", org2_observation, calibration)
    scored = org2_quality.assess_quality("OBS-1", org2_observation, calibration)

    decision = org2_policy.review_quality_record(scored.model_copy(update={"confidence": 0.2}))
    assert decision.approved
    assert "worth a human read" in decision.concerns[0]


# --- divergence flags -------------------------------------------------------


def _flag(**overrides) -> DivergenceFlagRequest:
    base = dict(
        flag_id="DF-OBS-ORG2",
        org2_observation_id="OBS-ORG2",
        reference_observation_id="OBS-ORG1",
        reference_org=ORG1,
        divergence_metric=8.9,
        threshold=2.0,
        evidence_hash="a" * 64,
        detected_at=T0,
    )
    return DivergenceFlagRequest(**{**base, **overrides})


def _anchor_both_sides(org2_pipeline, ingestion, org2_observation, observation, calibration):
    org2_pipeline.ingest("OBS-ORG2", org2_observation, calibration)
    assert ingestion.submit_anchor(
        ingestion.prepare_anchor("OBS-ORG1", observation, calibration)
    ).committed


def test_an_evidenced_disagreement_is_approved(
    org2_pipeline, org2_policy, org2_registered, registered, ingestion,
    org2_observation, observation, calibration,
):
    _anchor_both_sides(org2_pipeline, ingestion, org2_observation, observation, calibration)

    assert org2_policy.review_divergence_flag(_flag()).approved


def test_a_flag_against_an_unreadable_reference_is_refused(
    org2_pipeline, org2_policy, org2_registered, org2_observation, calibration
):
    """Org2 cannot evidence a disagreement with a reading that is not there."""
    org2_pipeline.ingest("OBS-ORG2", org2_observation, calibration)

    decision = org2_policy.review_divergence_flag(_flag())
    assert not decision.approved
    assert "is not on the ledger" in decision.reasons[0]


def test_a_flag_that_does_not_exceed_its_own_threshold_is_refused(
    org2_pipeline, org2_policy, org2_registered, registered, ingestion,
    org2_observation, observation, calibration,
):
    _anchor_both_sides(org2_pipeline, ingestion, org2_observation, observation, calibration)

    decision = org2_policy.review_divergence_flag(_flag(divergence_metric=0.5))
    assert not decision.approved
    assert "does not exceed the threshold" in decision.reasons[0]


def test_a_flag_about_an_observation_org2_does_not_own_is_refused(
    org2_policy, org2_registered, registered, ingestion, observation, calibration
):
    assert ingestion.submit_anchor(
        ingestion.prepare_anchor("OBS-ORG1", observation, calibration)
    ).committed

    decision = org2_policy.review_divergence_flag(_flag(org2_observation_id="OBS-ORG1"))
    assert not decision.approved
    assert f"is not owned by {ORG2}" in decision.reasons[0]


# --- answering requests for Org2 data ---------------------------------------


def _submit_as_org3(ledger, request: AccessRequest) -> AccessRequest:
    submission = AccessRequestSubmission(
        request_id=request.id,
        target_org=request.target_org,
        purpose=request.purpose,
        justification=request.justification,
        requested=request.requested,
        valid_until=request.valid_until,
    )
    receipt = ledger.connect_as(ORG3).submit(
        Fn.CREATE_ACCESS_REQUEST, request=submission.model_dump(mode="json")
    )
    assert receipt.committed, receipt.message
    return request


def _request(**overrides) -> AccessRequest:
    base = dict(
        id="AR-100",
        owner_org=ORG3,
        created_at=T0,
        created_by_tx="tx-1",
        target_org=ORG2,
        purpose="research",
        justification="Microclimate comparison across the Al Majaz corridor.",
        requested=[AssetRef(owner_org=ORG2, doc_type=DocType.SENSOR, asset_id=SENSOR_ID)],
    )
    return AccessRequest(**{**base, **overrides})


def test_a_reasonable_request_for_org2_data_is_approved(org2_policy, org2_registered):
    assert org2_policy.review_access_request(_request()).approved


def test_org2_will_not_grant_access_to_org1_data(org2_policy, org2_registered):
    decision = org2_policy.review_access_request(
        _request(requested=[
            AssetRef(owner_org=ORG2, doc_type=DocType.SENSOR, asset_id=SENSOR_ID),
            AssetRef(owner_org=ORG1, doc_type=DocType.STATION, asset_id="ST-SHJ-001"),
        ])
    )

    assert not decision.approved
    assert "must answer for their own data" in decision.reasons[0]


def test_compliance_sensitive_data_is_not_released_by_the_agent(
    org2_policy, org2_ingestion, sensor_request
):
    """Section 3: air-quality data belongs in a restricted private collection,
    so the agent refers it rather than releasing it on its own authority."""
    assert org2_ingestion.register_sensor(
        sensor_request.model_copy(update={"sensor_id": "SN-AQ-REG", "kind": "air-quality"})
    ).committed

    decision = org2_policy.review_access_request(
        _request(requested=[
            AssetRef(owner_org=ORG2, doc_type=DocType.SENSOR, asset_id="SN-AQ-REG")
        ])
    )

    assert not decision.approved
    assert "compliance-sensitive" in decision.reasons[0]


def test_an_observation_inherits_the_restriction_of_its_sensor(
    org2_policy, org2_pipeline, org2_ingestion, sensor_request, org2_observation, calibration
):
    """Releasing the reading releases the same regulated measurement, so the
    restriction cannot be sidestepped by asking for the anchor instead."""
    assert org2_ingestion.register_sensor(
        sensor_request.model_copy(update={"sensor_id": "SN-AQ-REG", "kind": "air-quality"})
    ).committed
    regulated = org2_observation.model_copy(
        update={"station_id": "SN-AQ-REG", "instrument_id": "SN-AQ-REG"}
    )
    assert org2_pipeline.ingest("OBS-AQ-1", regulated, calibration).committed

    decision = org2_policy.review_access_request(
        _request(requested=[
            AssetRef(owner_org=ORG2, doc_type=DocType.OBSERVATION_ANCHOR, asset_id="OBS-AQ-1")
        ])
    )

    assert not decision.approved
    assert "compliance-sensitive" in decision.reasons[0]


def test_the_answer_is_written_into_org2s_own_namespace(
    org2_policy, org2_registered, org2_ledger, ledger
):
    request = _submit_as_org3(ledger, _request())
    receipt = org2_policy.respond_to_access_request(
        request, org2_policy.review_access_request(request)
    )

    assert receipt.committed
    assert receipt.payload["owner_org"] == ORG2
    assert receipt.payload["requester_org"] == ORG3


def test_pending_requests_exclude_ones_already_answered(
    org2_policy, org2_registered, ledger
):
    request = _submit_as_org3(ledger, _request())
    assert [r.id for r in org2_policy.pending_access_requests()] == ["AR-100"]

    org2_policy.respond_to_access_request(request, org2_policy.review_access_request(request))
    assert org2_policy.pending_access_requests() == []

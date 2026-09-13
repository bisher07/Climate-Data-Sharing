"""Co-endorsement: reviewing records other organizations build from your data.

Sections 2 and 3 require each organization to co-endorse composite records
that cite its data, and forbid endorsing anything that does not. The review
inspects citations — existence, hash, grant — and never the record's
conclusion.
"""

from __future__ import annotations

from datetime import timedelta

from agent_prototype.ledger import Fn
from agent_prototype.shared.models import (
    AccessRequestSubmission,
    AssetRef,
    CitedInput,
    CompositeProposal,
    DocType,
)

from tests.conftest import ORG1, ORG2, ORG3, SENSOR_ID, T0


def _anchor(ingestion, observation, calibration, observation_id: str = "OBS-ORG1") -> str:
    prepared = ingestion.prepare_anchor(observation_id, observation, calibration)
    assert ingestion.submit_anchor(prepared).committed
    return prepared.data_hash


def _grant(ledger, owner_policy, *, requester: str, ref: AssetRef, valid_until=None):
    """`requester` asks for `ref`; the owner's policy agent answers."""
    ledger.connect_as(requester).submit(Fn.CREATE_ACCESS_REQUEST, request=AccessRequestSubmission(
        request_id=f"AR-{requester}-{ref.asset_id}", target_org=ref.owner_org,
        purpose="research",
        justification="Derived heat-island product for the Sharjah coastal strip.",
        requested=[ref], valid_until=valid_until,
    ).model_dump(mode="json"))
    request = owner_policy.pending_access_requests()[0]
    return owner_policy.respond_to_access_request(
        request, owner_policy.review_access_request(request)
    )


def _anchor_ref(owner: str, observation_id: str) -> AssetRef:
    return AssetRef(owner_org=owner, doc_type=DocType.OBSERVATION_ANCHOR, asset_id=observation_id)


def _proposal(*cited: CitedInput, proposer: str = ORG3, kind: str = "derived_product"):
    return CompositeProposal(
        proposal_id="CP-001", proposer_org=proposer, record_kind=kind, cited=list(cited)
    )


# --- Org1 ---------------------------------------------------------------------


def test_org1_co_endorses_a_correct_citation_by_a_granted_proposer(
    ingestion, policy, registered, ledger, observation, calibration, station_request
):
    data_hash = _anchor(ingestion, observation, calibration)
    _grant(ledger, policy, requester=ORG3,
           ref=AssetRef(owner_org=ORG1, doc_type=DocType.STATION, asset_id="ST-SHJ-001"))

    decision = policy.review_composite(
        _proposal(CitedInput(ref=_anchor_ref(ORG1, "OBS-ORG1"), data_hash=data_hash))
    )

    assert decision.approved, decision.reasons


def test_org1_has_no_standing_on_a_record_that_cites_none_of_its_data(policy):
    """Section 2: Org1 cannot endorse Org3-derived products unless its data is
    being referenced."""
    decision = policy.review_composite(
        _proposal(CitedInput(ref=_anchor_ref(ORG2, "OBS-ORG2"), data_hash="a" * 64))
    )

    assert not decision.approved
    assert "has no standing to endorse it" in decision.reasons[0]


def test_a_citation_of_an_asset_that_does_not_exist_is_refused(policy, registered):
    decision = policy.review_composite(
        _proposal(CitedInput(ref=_anchor_ref(ORG1, "OBS-IMAGINARY"), data_hash="a" * 64))
    )

    assert not decision.approved
    assert "does not exist" in decision.reasons[0]


def test_a_citation_with_the_wrong_hash_is_refused(
    ingestion, policy, registered, ledger, observation, calibration
):
    """Lineage has to point at the version that was anchored, not a similar one."""
    _anchor(ingestion, observation, calibration)
    _grant(ledger, policy, requester=ORG3,
           ref=AssetRef(owner_org=ORG1, doc_type=DocType.STATION, asset_id="ST-SHJ-001"))

    decision = policy.review_composite(
        _proposal(CitedInput(ref=_anchor_ref(ORG1, "OBS-ORG1"), data_hash="f" * 64))
    )

    assert not decision.approved
    assert "but the ledger holds" in decision.reasons[0]


def test_a_citation_without_a_hash_is_refused(
    ingestion, policy, registered, ledger, observation, calibration
):
    _anchor(ingestion, observation, calibration)
    _grant(ledger, policy, requester=ORG3,
           ref=AssetRef(owner_org=ORG1, doc_type=DocType.STATION, asset_id="ST-SHJ-001"))

    decision = policy.review_composite(_proposal(CitedInput(ref=_anchor_ref(ORG1, "OBS-ORG1"))))

    assert not decision.approved
    assert "without a hash" in decision.reasons[0]


def test_a_proposer_that_was_never_granted_the_data_is_refused(
    ingestion, policy, registered, observation, calibration
):
    """Section 5: a derived product is acceptable only if its inputs were
    legitimately obtained."""
    data_hash = _anchor(ingestion, observation, calibration)

    decision = policy.review_composite(
        _proposal(CitedInput(ref=_anchor_ref(ORG1, "OBS-ORG1"), data_hash=data_hash))
    )

    assert not decision.approved
    assert "holds no current grant from Org1MSP" in decision.reasons[0]


def test_an_expired_grant_does_not_support_a_citation(
    ingestion, policy, registered, ledger, observation, calibration
):
    data_hash = _anchor(ingestion, observation, calibration)
    _grant(ledger, policy, requester=ORG3,
           ref=AssetRef(owner_org=ORG1, doc_type=DocType.STATION, asset_id="ST-SHJ-001"),
           valid_until=T0 - timedelta(days=1))

    decision = policy.review_composite(
        _proposal(CitedInput(ref=_anchor_ref(ORG1, "OBS-ORG1"), data_hash=data_hash))
    )

    assert not decision.approved


def test_org1_reviews_only_its_own_citations(
    ingestion, policy, registered, ledger, observation, calibration
):
    """A citation of Org2 data is Org2's to review. Org1 neither vouches for it
    nor refuses because of it."""
    data_hash = _anchor(ingestion, observation, calibration)
    _grant(ledger, policy, requester=ORG3,
           ref=AssetRef(owner_org=ORG1, doc_type=DocType.STATION, asset_id="ST-SHJ-001"))

    decision = policy.review_composite(_proposal(
        CitedInput(ref=_anchor_ref(ORG1, "OBS-ORG1"), data_hash=data_hash),
        CitedInput(ref=_anchor_ref(ORG2, "OBS-NOT-ORG1S-BUSINESS"), data_hash="a" * 64),
    ))

    assert decision.approved, decision.reasons


def test_org1_co_endorses_a_divergence_flag_against_its_own_reading(
    ingestion, policy, registered, org2_granted, observation, calibration
):
    """The property that keeps disagreement visible. Org2's flag says Org1's
    reading is 8.5 degC away from its own — and Org1 cannot refuse to endorse it
    on those grounds, because the proposal never tells Org1 what it concludes."""
    data_hash = _anchor(ingestion, observation, calibration)

    decision = policy.review_composite(_proposal(
        CitedInput(ref=_anchor_ref(ORG1, "OBS-ORG1"), data_hash=data_hash),
        proposer=ORG2, kind="divergence_flag",
    ))

    assert decision.approved, decision.reasons


def test_a_proposal_has_no_field_for_what_the_record_concludes():
    assert set(CompositeProposal.model_fields) == {
        "proposal_id", "proposer_org", "record_kind", "cited",
    }


# --- Org2 ---------------------------------------------------------------------


def test_org2_co_endorses_a_record_citing_its_granted_sensor_reading(
    org2_pipeline, org2_policy, org2_registered, ledger, org2_observation, calibration
):
    result = org2_pipeline.ingest("OBS-ORG2", org2_observation, calibration)
    receipt = _grant(ledger, org2_policy, requester=ORG3,
                     ref=AssetRef(owner_org=ORG2, doc_type=DocType.SENSOR, asset_id=SENSOR_ID))
    assert receipt.payload["approved"]

    decision = org2_policy.review_composite(_proposal(
        CitedInput(ref=_anchor_ref(ORG2, "OBS-ORG2"), data_hash=result.prepared.data_hash)
    ))

    assert decision.approved, decision.reasons


def test_org2_will_not_co_endorse_use_of_compliance_sensitive_readings(
    org2_pipeline, org2_policy, org2_ingestion, sensor_request, ledger,
    org2_observation, calibration,
):
    """No automatic grant is ever issued for regulated air-quality data, so a
    record citing it fails co-endorsement without a separate rule."""
    org2_ingestion.register_sensor(
        sensor_request.model_copy(update={"sensor_id": "SN-AQ-REG", "kind": "air-quality"})
    )
    regulated = org2_observation.model_copy(
        update={"station_id": "SN-AQ-REG", "instrument_id": "SN-AQ-REG"}
    )
    result = org2_pipeline.ingest("OBS-AQ-1", regulated, calibration)
    receipt = _grant(ledger, org2_policy, requester=ORG3,
                     ref=AssetRef(owner_org=ORG2, doc_type=DocType.SENSOR, asset_id="SN-AQ-REG"))
    assert receipt.payload["approved"] is False

    decision = org2_policy.review_composite(_proposal(
        CitedInput(ref=_anchor_ref(ORG2, "OBS-AQ-1"), data_hash=result.prepared.data_hash)
    ))

    assert not decision.approved
    assert "holds no current grant from Org2MSP" in decision.reasons[0]


def test_org2_has_no_standing_on_a_record_citing_only_org1_data(org2_policy):
    decision = org2_policy.review_composite(
        _proposal(CitedInput(ref=_anchor_ref(ORG1, "OBS-ORG1"), data_hash="a" * 64))
    )

    assert not decision.approved
    assert "has no standing to endorse it" in decision.reasons[0]

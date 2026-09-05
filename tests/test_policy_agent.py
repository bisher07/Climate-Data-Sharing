"""Org1 Policy / Endorsement Agent: what Org1 will and will not stand behind."""

from __future__ import annotations

from datetime import timedelta

from agent_prototype.agents.org1 import PolicyEndorsementAgent, PreparedAnchor
from agent_prototype.ledger import Fn
from agent_prototype.shared.models import (
    AccessRequest,
    AccessRequestSubmission,
    AssetRef,
    DocType,
    Variable,
)

from tests.conftest import ORG1, ORG3, T0


# --- reviewing Org1's own records -------------------------------------------


def test_a_well_formed_anchor_is_approved(ingestion, policy, registered, observation, calibration):
    prepared = ingestion.prepare_anchor("OBS-1", observation, calibration)

    decision = policy.review_anchor(prepared)
    assert decision.approved
    assert decision.concerns == ()


def test_an_anchor_that_misdescribes_its_record_is_refused(
    ingestion, policy, registered, observation, calibration
):
    """The reviewing agent recomputes the hash rather than trusting the
    ingesting agent, so a swapped payload is caught inside Org1."""
    prepared = ingestion.prepare_anchor("OBS-1", observation, calibration)
    swapped = PreparedAnchor(
        request=prepared.request,
        record=observation.model_copy(
            update={"variables": {**observation.variables,
                                  "air_temperature": Variable(value=99.0, unit="degC")}}
        ),
        data_hash=prepared.data_hash,
    )

    decision = policy.review_anchor(swapped)
    assert not decision.approved
    assert "does not match the raw record" in decision.reasons[0]


def test_an_anchor_for_an_unregistered_station_is_refused(
    ingestion, policy, observation, calibration
):
    prepared = ingestion.prepare_anchor("OBS-1", observation, calibration)

    decision = policy.review_anchor(prepared)
    assert not decision.approved
    assert "is not registered by Org1MSP" in decision.reasons[0]


def test_an_instrument_sited_at_another_station_is_refused(
    ingestion, policy, registered, observation, calibration, instrument_request, station_request
):
    ingestion.register_station(station_request.model_copy(update={"station_id": "ST-OTHER"}))
    ingestion.register_instrument(
        instrument_request.model_copy(
            update={"instrument_id": "IN-ELSEWHERE", "station_id": "ST-OTHER"}
        )
    )
    prepared = ingestion.prepare_anchor(
        "OBS-1", observation.model_copy(update={"instrument_id": "IN-ELSEWHERE"}), calibration
    )

    decision = policy.review_anchor(prepared)
    assert not decision.approved
    assert "belongs to station 'ST-OTHER'" in decision.reasons[0]


def test_stale_calibration_is_surfaced_but_does_not_block(
    ingestion, ledger, registered, observation, calibration, clock
):
    """Section 9: make doubt visible rather than suppress the record."""
    strict = PolicyEndorsementAgent(
        "org1.policy", ledger, clock=clock, max_calibration_age=timedelta(days=1)
    )
    prepared = ingestion.prepare_anchor("OBS-1", observation, calibration)

    decision = strict.review_anchor(prepared)
    assert decision.approved
    assert "calibration is" in decision.concerns[0]
    assert "approved with concerns" in decision.summary


def test_org1_never_approves_another_organizations_data(policy):
    """Section 2: never approve another organization's data on its behalf."""
    decision = policy.review_foreign_data("Org2MSP", "an Org2 air-quality reading")

    assert not decision.approved
    assert "has no standing to approve it" in decision.reasons[0]
    assert policy.review_foreign_data(ORG1, "an Org1 observation").approved


# --- answering other organizations' access requests -------------------------


def _submit(ledger, request: AccessRequest) -> AccessRequest:
    """Put a request on the ledger as Org3 would. Org3's own Access
    Negotiation Agent will do this properly once it exists."""
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
        id="AR-001",
        owner_org=ORG3,
        created_at=T0,
        created_by_tx="tx-1",
        target_org=ORG1,
        purpose="research",
        justification="Urban heat island study across the Sharjah coastal strip.",
        requested=[AssetRef(owner_org=ORG1, doc_type=DocType.STATION, asset_id="ST-SHJ-001")],
    )
    return AccessRequest(**{**base, **overrides})


def test_a_reasonable_request_for_org1_data_is_approved(policy, registered):
    decision = policy.review_access_request(_request())

    assert decision.approved


def test_a_request_addressed_elsewhere_is_refused(policy, registered):
    decision = policy.review_access_request(_request(target_org="Org2MSP"))

    assert not decision.approved
    assert "addressed to Org2MSP" in decision.reasons[0]


def test_org1_will_not_grant_access_to_another_orgs_data(policy, registered):
    """Even bundled with its own. Org2 must answer for Org2's sensors."""
    decision = policy.review_access_request(
        _request(requested=[
            AssetRef(owner_org=ORG1, doc_type=DocType.STATION, asset_id="ST-SHJ-001"),
            AssetRef(owner_org="Org2MSP", doc_type=DocType.STATION, asset_id="SN-AQ-77"),
        ])
    )

    assert not decision.approved
    assert "must answer for their own data" in decision.reasons[0]


def test_a_request_for_data_that_does_not_exist_is_refused(policy, registered):
    decision = policy.review_access_request(
        _request(requested=[
            AssetRef(owner_org=ORG1, doc_type=DocType.STATION, asset_id="ST-IMAGINARY")
        ])
    )

    assert not decision.approved
    assert "do not exist" in decision.reasons[0]


def test_an_unrecognised_purpose_goes_to_a_human(policy, registered):
    """The agent does not invent policy for cases it was not given."""
    decision = policy.review_access_request(_request(purpose="commercial-resale"))

    assert not decision.approved
    assert "refer to a human reviewer" in decision.reasons[0]


def test_a_thin_justification_is_a_concern_not_a_refusal(policy, registered):
    decision = policy.review_access_request(_request(justification="need it"))

    assert decision.approved
    assert "worth a human read" in decision.concerns[0]


def test_the_answer_is_written_into_org1s_own_namespace(policy, registered, ledger):
    """Section 12: Org1 cannot edit Org3's request record, so it writes its own
    decision asset instead."""
    request = _submit(ledger, _request())
    receipt = policy.respond_to_access_request(request, policy.review_access_request(request))

    assert receipt.committed
    function, arguments = policy.ledger.submitted[-1]
    assert function == "RespondToAccessRequest"
    assert arguments["request"]["request_id"] == "AR-001"
    assert arguments["request"]["approved"] is True


def test_a_refusal_grants_nothing(policy, registered, ledger):
    request = _submit(ledger, _request(purpose="commercial-resale"))
    receipt = policy.respond_to_access_request(request, policy.review_access_request(request))

    assert receipt.committed  # the answer is recorded
    assert receipt.payload["approved"] is False
    assert receipt.payload["granted"] == []  # but nothing is released


def test_pending_requests_exclude_ones_already_answered(policy, registered, ledger):
    request = _submit(ledger, _request())
    assert [r.id for r in policy.pending_access_requests()] == ["AR-001"]

    policy.respond_to_access_request(request, policy.review_access_request(request))
    assert policy.pending_access_requests() == []

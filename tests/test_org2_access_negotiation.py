"""Org2 Access Negotiation Agent: asking for data, and tracking what came back."""

from __future__ import annotations

from datetime import timedelta

import pytest

from agent_prototype.agents.org2 import NegotiationRefused
from agent_prototype.shared.models import AssetRef, DocType

from tests.conftest import ORG1, ORG2, T0

ORG1_STATION = AssetRef(owner_org=ORG1, doc_type=DocType.STATION, asset_id="ST-SHJ-001")


def _draft(agent, **overrides):
    base = dict(
        target_org=ORG1,
        purpose="validation",
        justification="Cross-checking Org2 microclimate nodes against the coastal AWS.",
        requested=[ORG1_STATION],
    )
    return agent.draft_request(overrides.pop("request_id", "AR-ORG2-1"), **{**base, **overrides})


# --- asking -----------------------------------------------------------------


def test_a_coherent_request_is_drafted_and_submitted(org2_negotiation):
    submission = _draft(org2_negotiation)
    receipt = org2_negotiation.submit_request(submission)

    assert receipt.committed
    assert receipt.payload["owner_org"] == ORG2
    assert receipt.payload["target_org"] == ORG1


def test_the_submission_carries_no_requester_field(org2_negotiation):
    """The requester is whoever's identity submits it, so there is nothing here
    for Org2 to misstate about who is asking."""
    submission = _draft(org2_negotiation)

    assert "requester_org" not in submission.model_dump()


def test_org2_cannot_address_a_request_to_itself(org2_negotiation):
    with pytest.raises(NegotiationRefused, match="cannot address an access request to itself"):
        _draft(org2_negotiation, target_org=ORG2,
               requested=[AssetRef(owner_org=ORG2, doc_type=DocType.SENSOR, asset_id="SN-1")])


def test_a_request_that_asks_the_wrong_organization_is_refused(org2_negotiation):
    """Org1 cannot answer for Org3's data, so the request is never sent."""
    with pytest.raises(NegotiationRefused, match="answers for its own data"):
        _draft(org2_negotiation, requested=[
            ORG1_STATION,
            AssetRef(owner_org="Org3MSP", doc_type=DocType.STATION, asset_id="X-1"),
        ])


def test_a_refused_draft_reaches_no_ledger(org2_negotiation, org2_ledger):
    with pytest.raises(NegotiationRefused):
        _draft(org2_negotiation, target_org=ORG2,
               requested=[AssetRef(owner_org=ORG2, doc_type=DocType.SENSOR, asset_id="SN-1")])

    assert org2_ledger.submitted == []


# --- reading the answers ----------------------------------------------------


def _answered(org2_negotiation, policy, approve: bool = True):
    """Org2 asks; Org1's policy agent answers."""
    org2_negotiation.submit_request(
        _draft(org2_negotiation, purpose="validation" if approve else "commercial-resale")
    )
    pending = policy.pending_access_requests()
    assert len(pending) == 1
    request = pending[0]
    return policy.respond_to_access_request(request, policy.review_access_request(request))


def test_an_unanswered_request_is_outstanding(org2_negotiation):
    org2_negotiation.submit_request(_draft(org2_negotiation))

    assert [r.id for r in org2_negotiation.outstanding_requests()] == ["AR-ORG2-1"]
    assert org2_negotiation.granted_assets() == []


def test_an_answered_request_is_no_longer_outstanding(
    org2_negotiation, policy, registered
):
    _answered(org2_negotiation, policy)

    assert org2_negotiation.outstanding_requests() == []


def test_an_approval_is_reported_as_granted(org2_negotiation, policy, registered):
    _answered(org2_negotiation, policy)

    assert org2_negotiation.granted_assets() == [ORG1_STATION]
    assert org2_negotiation.may_use(ORG1_STATION)


def test_a_refusal_grants_nothing(org2_negotiation, policy, registered):
    receipt = _answered(org2_negotiation, policy, approve=False)

    assert receipt.payload["approved"] is False
    assert org2_negotiation.granted_assets() == []
    assert not org2_negotiation.may_use(ORG1_STATION)


def test_an_expired_approval_grants_nothing(
    org2_negotiation, policy, registered, org2_ledger
):
    """A grant that has run out is indistinguishable from one never given."""
    org2_negotiation.submit_request(
        _draft(org2_negotiation, valid_until=T0 - timedelta(days=1))
    )
    request = policy.pending_access_requests()[0]
    policy.respond_to_access_request(request, policy.review_access_request(request))

    assert org2_negotiation.decisions()[0].approved
    assert org2_negotiation.granted_assets() == []
    assert not org2_negotiation.may_use(ORG1_STATION)


def test_org2_needs_no_grant_for_its_own_data(org2_negotiation):
    own = AssetRef(owner_org=ORG2, doc_type=DocType.SENSOR, asset_id="SN-AQ-077")

    assert org2_negotiation.may_use(own)


def test_the_agent_grants_nothing_on_its_own_authority(
    org2_negotiation, policy, registered
):
    """Section 16: the answer is written by the deciding organization, into
    that organization's namespace. Org2 only reads it back."""
    _answered(org2_negotiation, policy)
    decision = org2_negotiation.decisions()[0]

    assert decision.owner_org == ORG1
    assert decision.requester_org == ORG2


# --- what a grant covers -----------------------------------------------------


def test_a_station_grant_covers_the_readings_that_station_produced(
    org2_granted, ingestion, observation, calibration
):
    """Nobody requests hourly readings one by one. A grant on the site covers
    what the site measures."""
    assert ingestion.submit_anchor(
        ingestion.prepare_anchor("OBS-ORG1", observation, calibration)
    ).committed

    reading = AssetRef(owner_org=ORG1, doc_type=DocType.OBSERVATION_ANCHOR, asset_id="OBS-ORG1")
    assert org2_granted.may_use(reading)


def test_a_decision_cannot_cover_data_its_author_does_not_own():
    """Defence in depth: even if a decision granting someone else's data were
    ever stored, it would not count."""
    from agent_prototype.shared.models import AccessDecision

    forged = AccessDecision(
        id="AD-FORGED", owner_org="Org3MSP", created_at=T0, created_by_tx="tx-1",
        request_id="AR-1", requester_org=ORG2, approved=True, reason="granted",
        granted=[ORG1_STATION],
    )

    assert not forged.covers(ORG1_STATION, now=T0)

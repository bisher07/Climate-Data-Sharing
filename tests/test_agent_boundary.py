"""The seam between agents and the ledger.

Agents propose and reason; the chaincode decides (Section 16). These tests
check the agent side of that boundary: that an agent's approval carries no
weight of its own, that it cannot act for another organization, and that it
leaves enough evidence behind to be audited.
"""

from __future__ import annotations

import pytest

from agent_prototype.agents.org1 import (
    IngestionProvenanceAgent,
    Org1IngestionPipeline,
    PolicyEndorsementAgent,
    Stage,
)
from agent_prototype.ledger import InMemoryLedger, LedgerClient
from agent_prototype.shared.models import Variable

from tests.conftest import ORG3


def test_the_fake_ledger_satisfies_the_port():
    """The interface agents are written against is the one the glue step will
    implement. If this drifts, the integration breaks silently."""
    assert isinstance(InMemoryLedger("Org1MSP"), LedgerClient)


# --- an agent acts for exactly one organization ------------------------------


def test_an_agent_takes_its_organization_from_its_connection(ledger, clock):
    """There is no argument through which an agent could claim to be another
    organization, which is why impersonation is not an agent-level concern."""
    org1_agent = IngestionProvenanceAgent("a", ledger, clock=clock)
    org3_agent = IngestionProvenanceAgent("a", ledger.connect_as(ORG3), clock=clock)

    assert org1_agent.org_id == "Org1MSP"
    assert org3_agent.org_id == ORG3


def test_a_pipeline_cannot_span_two_organizations(ledger, clock):
    ingestion = IngestionProvenanceAgent("org1.ingestion", ledger, clock=clock)
    foreign_policy = PolicyEndorsementAgent(
        "org3.policy", ledger.connect_as(ORG3), clock=clock
    )

    with pytest.raises(ValueError, match="different organizations"):
        Org1IngestionPipeline(ingestion, foreign_policy)


# --- agent approval grants nothing -------------------------------------------


def test_agent_approval_does_not_make_the_ledger_accept_a_transaction(
    pipeline, ingestion, policy, registered, observation, calibration
):
    """The agent approves a duplicate anchor — every check it knows how to make
    passes — and the ledger refuses it anyway."""
    assert pipeline.ingest("OBS-1", observation, calibration).committed

    prepared = ingestion.prepare_anchor("OBS-1", observation, calibration)
    assert policy.review_anchor(prepared).approved  # the agent is satisfied

    receipt = ingestion.submit_anchor(prepared)
    assert not receipt.committed  # the ledger is not
    assert "already exists" in receipt.message


def test_a_ledger_refusal_is_reported_as_a_stage_not_swallowed(
    pipeline, observation, calibration
):
    """No station is registered, so the policy agent stops it first — the
    failure is attributed to the stage that caught it."""
    result = pipeline.ingest("OBS-1", observation, calibration)

    assert result.stage is Stage.POLICY_REVIEW
    assert result.receipt is None
    assert "is not registered" in result.error


def test_screening_stops_a_record_before_any_ledger_call(
    pipeline, registered, observation, calibration
):
    absurd = observation.model_copy(
        update={"variables": {"air_temperature": Variable(value=412.0, unit="degC")}}
    )
    before = len(pipeline.ingestion.ledger.submitted)

    result = pipeline.ingest("OBS-BAD", absurd, calibration)
    assert result.stage is Stage.SCREENING
    assert "outside the plausible range" in result.error
    assert len(pipeline.ingestion.ledger.submitted) == before


# --- auditability -------------------------------------------------------------


def test_every_step_leaves_an_audit_record(pipeline, registered, observation, calibration):
    """Section 12: important agent actions produce auditable evidence."""
    pipeline.ingest("OBS-1", observation, calibration)

    assert [e.action for e in pipeline.ingestion.audit_trail] == [
        "register_station", "register_instrument", "prepare_anchor", "submit_anchor",
    ]
    review = pipeline.policy.audit_trail[-1]
    assert review.action == "review_anchor"
    assert review.outcome == "APPROVED"
    assert review.org_id == "Org1MSP"


def test_the_audit_record_pins_what_was_judged(
    ingestion, policy, registered, observation, calibration
):
    """The reviewer's `input_hash` lets an auditor confirm the thing that was
    approved is the thing that was submitted."""
    prepared = ingestion.prepare_anchor("OBS-1", observation, calibration)
    policy.review_anchor(prepared)
    ingestion.submit_anchor(prepared)

    reviewed = policy.audit_trail[-1].input_hash
    submitted = ingestion.audit_trail[-1].input_hash
    assert reviewed == submitted


def test_refusals_are_recorded_with_their_reasons(policy, ingestion, observation, calibration):
    prepared = ingestion.prepare_anchor("OBS-1", observation, calibration)
    policy.review_anchor(prepared)

    entry = policy.audit_trail[-1]
    assert entry.outcome == "REFUSED"
    assert any("not registered" in reason for reason in entry.detail["reasons"])


def test_a_rejected_transaction_is_still_audited(ingestion, observation, calibration):
    """A refusal must leave a trace, so that rejected records can be counted
    for the evaluation in Section 15."""
    ingestion.submit_anchor(ingestion.prepare_anchor("OBS-1", observation, calibration))

    entry = ingestion.audit_trail[-1]
    assert entry.action == "submit_anchor"
    assert entry.outcome == "REJECTED"
    assert "station" in entry.detail["message"]

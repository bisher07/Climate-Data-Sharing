"""Org1 Policy / Endorsement Agent.

This agent decides what Org1 is willing to put its name on: whether to propose
one of its own records, and how to answer another organization's request for
Org1 data (Section 2).

Its approval is advisory in one direction only. It can stop Org1 from
proposing something, but it grants nothing — the chaincode re-checks every rule
independently, so an agent that is compromised or simply wrong cannot widen
Org1's authority. That asymmetry is the point of Section 16's "agents propose,
Fabric enforces", and it is why nothing in this file is written as though its
verdict were final.

Deterministic throughout: these are ownership, provenance and referential
checks. Section 10 marks access negotiation as a place where a language model
*could* eventually help — `review_access_request` is where that would attach,
and the deterministic checks around it would still have to pass first.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from agent_prototype.agents.base import Agent, Decision
from agent_prototype.agents.org1.ingestion_provenance import SCHEMA_VERSION, PreparedAnchor
from agent_prototype.ledger.port import Fn, TxReceipt
from agent_prototype.shared.models import (
    AccessDecision,
    AccessDecisionRequest,
    AccessRequest,
    AssetStatus,
    CompositeProposal,
    DocType,
)
from agent_prototype.shared.utilities.canonical import hash_payload
from agent_prototype.shared.utilities.clock import Clock

__all__ = ["PolicyEndorsementAgent", "DEFAULT_MAX_CALIBRATION_AGE", "DEFAULT_PURPOSES"]

DEFAULT_MAX_CALIBRATION_AGE = timedelta(days=365)

# Purposes Org1 will consider. Anything else needs a human, which is the honest
# position for a research prototype: the agent does not invent policy.
DEFAULT_PURPOSES = frozenset(
    {"research", "validation", "public-reporting", "emergency-response"}
)


class PolicyEndorsementAgent(Agent):
    def __init__(
        self,
        agent_id: str,
        ledger,
        *,
        clock: Clock | None = None,
        max_calibration_age: timedelta = DEFAULT_MAX_CALIBRATION_AGE,
        permitted_purposes: frozenset[str] = DEFAULT_PURPOSES,
    ) -> None:
        super().__init__(agent_id, ledger, clock=clock)
        self.max_calibration_age = max_calibration_age
        self.permitted_purposes = permitted_purposes

    # ------------------------------------------------------------------
    # Org1's own records
    # ------------------------------------------------------------------

    def review_anchor(self, prepared: PreparedAnchor) -> Decision:
        """Decide whether Org1 should propose this observation anchor."""
        request = prepared.request
        reasons: list[str] = []
        concerns: list[str] = []

        # The anchor must actually describe the record it travels with. The
        # hash is recomputed rather than trusted, because the ingesting agent
        # is not more trustworthy than any other component.
        recomputed = hash_payload(prepared.record)
        if recomputed != request.data_hash:
            reasons.append(
                f"data_hash {request.data_hash[:12]}… does not match the raw record "
                f"({recomputed[:12]}…)"
            )

        if request.provenance.schema_version != SCHEMA_VERSION:
            reasons.append(
                f"provenance schema {request.provenance.schema_version!r} is not "
                f"{SCHEMA_VERSION!r}"
            )
        if not request.provenance.source_system.strip():
            reasons.append("provenance is missing a source system")

        # Referential integrity, read back from the ledger rather than assumed.
        station = self._own_asset(DocType.STATION, request.station_id)
        if station is None:
            reasons.append(f"station {request.station_id!r} is not registered by {self.org_id}")
        elif station.get("status") != AssetStatus.ACTIVE:
            reasons.append(f"station {request.station_id!r} is {station.get('status')}")

        instrument = self._own_asset(DocType.INSTRUMENT, request.instrument_id)
        if instrument is None:
            reasons.append(
                f"instrument {request.instrument_id!r} is not registered by {self.org_id}"
            )
        elif instrument["station_id"] != request.station_id:
            reasons.append(
                f"instrument {request.instrument_id!r} belongs to station "
                f"{instrument['station_id']!r}, not {request.station_id!r}"
            )

        # Stale calibration is recorded, not blocked (Section 9).
        age = request.provenance.ingested_at - request.calibration.calibrated_at
        if age > self.max_calibration_age:
            concerns.append(
                f"instrument calibration is {age.days} days old "
                f"(threshold {self.max_calibration_age.days} days)"
            )

        return self._decide("review_anchor", request, reasons, concerns,
                            observation_id=request.observation_id)

    def review_foreign_data(self, owner_org: str, description: str) -> Decision:
        """Org1 never approves another organization's data on its behalf."""
        reasons = (
            []
            if owner_org == self.org_id
            else [f"{description} is owned by {owner_org}; {self.org_id} has no standing "
                  "to approve it"]
        )
        return self._decide("review_foreign_data",
                            {"owner_org": owner_org, "description": description}, reasons, [])

    # ------------------------------------------------------------------
    # Co-endorsing records other organizations build from Org1 data
    # ------------------------------------------------------------------

    def review_composite(self, proposal: CompositeProposal) -> Decision:
        """Decide whether Org1 co-endorses a record that cites Org1 data.

        Section 2: Org1 must co-endorse composite records referencing its
        stations, and has no standing to endorse anything that does not.

        The review checks citations and nothing else — that each cited Org1
        asset exists, that the claimed hash is the one on the ledger, and that
        the proposer was granted it. The proposal carries no account of what
        the record concludes, so Org1 cannot withhold endorsement from a
        divergence flag because the flag disagrees with Org1.
        """
        reasons: list[str] = []
        own = [c for c in proposal.cited if c.ref.owner_org == self.org_id]
        if not own:
            reasons.append(
                f"proposal {proposal.proposal_id!r} cites no {self.org_id} data; "
                f"{self.org_id} has no standing to endorse it"
            )

        needs_grant = proposal.proposer_org != self.org_id
        decisions = [
            AccessDecision.model_validate(item)
            for item in self.ledger.evaluate(
                Fn.LIST_ACCESS_DECISIONS_FOR, requester_org=proposal.proposer_org
            )
        ] if own and needs_grant else []
        now = self.clock.now()

        for cited in own:
            asset = self._own_asset(cited.ref.doc_type, cited.ref.asset_id)
            if asset is None:
                reasons.append(f"cited {cited.ref} does not exist")
                continue
            stored = asset.get("data_hash")
            if stored is not None and cited.data_hash is None:
                reasons.append(f"cites {cited.ref} without a hash, so its version cannot be verified")
            elif stored is not None and cited.data_hash != stored:
                reasons.append(
                    f"cites {cited.ref} with hash {cited.data_hash[:12]}…, "
                    f"but the ledger holds {stored[:12]}…"
                )
            if needs_grant and not any(
                d.covers(cited.ref, now=now, site_id=asset.get("station_id")) for d in decisions
            ):
                reasons.append(
                    f"{proposal.proposer_org} holds no current grant from {self.org_id} "
                    f"for {cited.ref}"
                )

        return self._decide("review_composite", proposal, reasons, [],
                            proposal_id=proposal.proposal_id,
                            proposer=proposal.proposer_org,
                            record_kind=proposal.record_kind)

    # ------------------------------------------------------------------
    # Requests from other organizations for Org1 data
    # ------------------------------------------------------------------

    def pending_access_requests(self) -> list[AccessRequest]:
        """Requests addressed to Org1 that Org1 has not yet answered."""
        raw = self.ledger.evaluate(Fn.LIST_ACCESS_REQUESTS_FOR, target_org=self.org_id)
        return [AccessRequest.model_validate(item) for item in raw]

    def review_access_request(self, request: AccessRequest) -> Decision:
        """Decide whether Org1 will release the data another org has asked for.

        Org1's core observations are openly shareable (Section 2), so the
        default posture is permissive. The checks are about scope rather than
        secrecy: Org1 answers only for its own data, only what exists, and only
        what is not under embargo.
        """
        reasons: list[str] = []
        concerns: list[str] = []

        if request.target_org != self.org_id:
            reasons.append(
                f"request {request.id!r} is addressed to {request.target_org}, not {self.org_id}"
            )

        if request.purpose not in self.permitted_purposes:
            reasons.append(
                f"purpose {request.purpose!r} is not one Org1 releases data for "
                f"automatically; refer to a human reviewer"
            )
        if len(request.justification.strip()) < 20:
            concerns.append("justification is thin enough to be worth a human read")

        # Org1 can only grant access to Org1's data. A request that reaches for
        # another organization's assets is not Org1's to answer, even in part.
        foreign = sorted({ref.owner_org for ref in request.requested if ref.owner_org != self.org_id})
        if foreign:
            reasons.append(
                f"request covers data owned by {', '.join(foreign)}; those organizations "
                "must answer for their own data"
            )

        missing = [
            str(ref)
            for ref in request.requested
            if ref.owner_org == self.org_id
            and self._own_asset(ref.doc_type, ref.asset_id) is None
        ]
        if missing:
            reasons.append(f"requested assets do not exist: {missing}")

        embargoed = []
        now = self.clock.now()
        for ref in request.requested:
            if missing or ref.owner_org != self.org_id:
                continue
            until = (self._own_asset(ref.doc_type, ref.asset_id) or {}).get("embargoed_until")
            # Compared as instants: ISO text sorts by its digits, so an embargo
            # written in Gulf time would otherwise outlast the same moment in UTC.
            if until is not None and now < datetime.fromisoformat(until):
                embargoed.append(f"{ref} until {until}")
        if embargoed:
            reasons.append(f"requested products are under embargo: {embargoed}")

        return self._decide("review_access_request", request, reasons, concerns,
                            request_id=request.id, requester=request.requester_org)

    def respond_to_access_request(
        self,
        request: AccessRequest,
        decision: Decision,
        *,
        decision_id: str | None = None,
        valid_until=None,
    ) -> TxReceipt:
        """Record Org1's answer on the ledger.

        The answer is a separate asset in Org1's namespace rather than an edit
        to the requester's record, because no organization writes into
        another's namespace (Section 12).
        """
        answer = AccessDecisionRequest(
            decision_id=decision_id or f"AD-{request.id}",
            request_id=request.id,
            requester_org=request.requester_org,
            approved=decision.approved,
            reason=decision.summary,
            granted=list(request.requested) if decision.approved else [],
            conditions=list(decision.concerns),
            valid_until=valid_until or request.valid_until,
        )
        return self.record_receipt(
            "respond_to_access_request",
            answer,
            self.ledger.submit(
                Fn.RESPOND_TO_ACCESS_REQUEST, request=answer.model_dump(mode="json")
            ),
        )

    # ------------------------------------------------------------------

    def _own_asset(self, doc_type: DocType, asset_id: str) -> dict | None:
        return self.ledger.evaluate(
            Fn.GET_ASSET, owner_org=self.org_id, doc_type=str(doc_type), asset_id=asset_id
        )

    def _decide(
        self, action: str, payload, reasons: list[str], concerns: list[str], **detail
    ) -> Decision:
        decision = Decision(
            approved=not reasons, reasons=tuple(reasons), concerns=tuple(concerns)
        )
        self.record(
            action,
            payload,
            "APPROVED" if decision.approved else "REFUSED",
            reasons=list(decision.reasons) or None,
            concerns=list(decision.concerns) or None,
            **detail,
        )
        return decision

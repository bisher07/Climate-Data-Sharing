"""Org2 Policy / Endorsement Agent.

Decides what Org2 is willing to put its name on: its own observation anchors,
the quality records and divergence flags its validation agent produces, and how
Org2 answers another organization's request for Org2 data (Section 3).

As with Org1's, its approval is advisory in one direction only — it can stop
Org2 from proposing something, but it grants nothing, because the chaincode
re-checks every rule independently (Section 16).

Org2's endorsement rules differ from Org1's in two ways that matter here.
Org2's site model is a single `Sensor` rather than Org1's station/instrument
pair, so referential checks look different. And Section 3 places
compliance-sensitive air-quality data in a restricted private collection, so
Org2's answering posture is narrower than Org1's open-exchange default: some of
Org2's data is not the agent's to release automatically.

Deliberately not shared with Org1's policy agent despite the family
resemblance. These are separately deployed codebases owned by different
organizations; a shared base class would make Org1's policy a dependency of
Org2's, which is exactly the coupling the architecture is trying to avoid.
"""

from __future__ import annotations

from datetime import timedelta

from agent_prototype.agents.base import Agent, Decision
from agent_prototype.agents.org2.ingestion_provenance import SCHEMA_VERSION, PreparedAnchor
# Shared with the validation agent on purpose: within one organization there is
# one answer to "when is a calibration too old". The two agents respond to it
# differently — quality lowers confidence, policy raises a concern — but they
# should not be able to disagree about the threshold itself.
from agent_prototype.agents.org2.quality_validation import DEFAULT_MAX_CALIBRATION_AGE
from agent_prototype.ledger.port import Fn, TxReceipt
from agent_prototype.shared.models import (
    AccessDecision,
    AccessDecisionRequest,
    AccessRequest,
    AssetStatus,
    CompositeProposal,
    DivergenceFlagRequest,
    DocType,
    QualityRecordRequest,
)
from agent_prototype.shared.utilities.canonical import hash_payload
from agent_prototype.shared.utilities.clock import Clock

__all__ = ["PolicyEndorsementAgent", "DEFAULT_PURPOSES", "DEFAULT_RESTRICTED_KINDS"]

DEFAULT_PURPOSES = frozenset(
    {"research", "validation", "public-reporting", "emergency-response"}
)

# Section 3: compliance-sensitive air-quality data belongs in a restricted
# private data collection, not in the open exchange. The agent will not release
# it on its own authority — a human decides, and the collection enforces.
DEFAULT_RESTRICTED_KINDS = frozenset({"air-quality"})

# Tolerance for clock skew between the validating agent and the ledger.
CLOCK_SKEW = timedelta(minutes=5)


class PolicyEndorsementAgent(Agent):
    def __init__(
        self,
        agent_id: str,
        ledger,
        *,
        clock: Clock | None = None,
        max_calibration_age: timedelta = DEFAULT_MAX_CALIBRATION_AGE,
        permitted_purposes: frozenset[str] = DEFAULT_PURPOSES,
        restricted_kinds: frozenset[str] = DEFAULT_RESTRICTED_KINDS,
    ) -> None:
        super().__init__(agent_id, ledger, clock=clock)
        self.max_calibration_age = max_calibration_age
        self.permitted_purposes = permitted_purposes
        self.restricted_kinds = restricted_kinds

    # ------------------------------------------------------------------
    # Org2's own observations
    # ------------------------------------------------------------------

    def review_anchor(self, prepared: PreparedAnchor) -> Decision:
        """Decide whether Org2 should propose this observation anchor."""
        request = prepared.request
        reasons: list[str] = []
        concerns: list[str] = []

        # Recomputed rather than trusted: the ingesting agent is not more
        # trustworthy than any other component.
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

        # Org2 has one combined Sensor asset, so both id fields on the anchor
        # name the same sensor. If they disagree, the anchor claims a
        # site/device pairing Org2 does not have.
        if request.station_id != request.instrument_id:
            reasons.append(
                f"anchor names sensor {request.station_id!r} and device "
                f"{request.instrument_id!r}; {self.org_id} registers one sensor per site"
            )

        sensor = self._own_asset(DocType.SENSOR, request.station_id)
        if sensor is None:
            reasons.append(f"sensor {request.station_id!r} is not registered by {self.org_id}")
        elif sensor.get("status") != AssetStatus.ACTIVE:
            reasons.append(f"sensor {request.station_id!r} is {sensor.get('status')}")

        age = request.provenance.ingested_at - request.calibration.calibrated_at
        if age > self.max_calibration_age:
            concerns.append(
                f"sensor calibration is {age.days} days old "
                f"(threshold {self.max_calibration_age.days} days)"
            )

        return self._decide("review_anchor", request, reasons, concerns,
                            observation_id=request.observation_id)

    # ------------------------------------------------------------------
    # What the validation agent produces
    # ------------------------------------------------------------------

    def review_quality_record(self, request: QualityRecordRequest) -> Decision:
        """Decide whether Org2 should publish this quality score.

        A quality record is a claim about a reading, so Org2 may only make one
        about a reading it owns. Judging another organization's raw data is
        what a divergence flag is for, and that says only "we disagree", not
        "they are wrong".
        """
        reasons: list[str] = []
        concerns: list[str] = []

        if self._own_asset(DocType.OBSERVATION_ANCHOR, request.observation_id) is None:
            reasons.append(
                f"observation {request.observation_id!r} is not owned by {self.org_id}; "
                "quality records are only for its own readings"
            )

        if request.validated_at > self.clock.now() + CLOCK_SKEW:
            reasons.append(
                f"validated_at {request.validated_at.isoformat()} is in the future"
            )

        if request.confidence < 0.5:
            concerns.append(
                f"confidence {request.confidence:.2f} is low enough to be worth a human read"
            )

        return self._decide("review_quality_record", request, reasons, concerns,
                            observation_id=request.observation_id,
                            quality_score=request.quality_score)

    def review_divergence_flag(self, request: DivergenceFlagRequest) -> Decision:
        """Decide whether Org2 should raise this disagreement on the ledger.

        Section 9: the flag makes disagreement visible; it does not adjudicate
        it. So the checks are about whether the disagreement is real and
        evidenced — both readings exist, and the measured gap actually exceeds
        the threshold being claimed — never about which side is correct.
        """
        reasons: list[str] = []

        if self._own_asset(DocType.OBSERVATION_ANCHOR, request.org2_observation_id) is None:
            reasons.append(
                f"observation {request.org2_observation_id!r} is not owned by {self.org_id}"
            )

        reference = self.ledger.evaluate(
            Fn.GET_ASSET,
            owner_org=request.reference_org,
            doc_type=str(DocType.OBSERVATION_ANCHOR),
            asset_id=request.reference_observation_id,
        )
        if reference is None:
            reasons.append(
                f"referenced observation {request.reference_observation_id!r} owned by "
                f"{request.reference_org} is not on the ledger"
            )

        if request.divergence_metric <= request.threshold:
            reasons.append(
                f"divergence {request.divergence_metric} does not exceed the threshold "
                f"{request.threshold} it is flagged against"
            )

        return self._decide("review_divergence_flag", request, reasons, [],
                            flag_id=request.flag_id,
                            reference_org=request.reference_org)

    def review_foreign_data(self, owner_org: str, description: str) -> Decision:
        """Org2 never approves another organization's data on its behalf."""
        reasons = (
            []
            if owner_org == self.org_id
            else [f"{description} is owned by {owner_org}; {self.org_id} has no standing "
                  "to approve it"]
        )
        return self._decide("review_foreign_data",
                            {"owner_org": owner_org, "description": description}, reasons, [])

    # ------------------------------------------------------------------
    # Co-endorsing records other organizations build from Org2 data
    # ------------------------------------------------------------------

    def review_composite(self, proposal: CompositeProposal) -> Decision:
        """Decide whether Org2 co-endorses a record that cites Org2 data.

        Section 3: Org2 co-endorses composite records involving its data, and
        cannot approve outputs on another organization's behalf.

        Citations only — existence, hash, and the proposer's grant. The
        proposal describes no conclusion, so a record that reflects badly on
        an Org2 sensor cannot be refused for doing so. Compliance-sensitive
        readings need no separate check: Org2 never grants them automatically,
        so a proposer citing one fails the grant test.
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
    # Requests from other organizations for Org2 data
    # ------------------------------------------------------------------

    def pending_access_requests(self) -> list[AccessRequest]:
        """Requests addressed to Org2 that Org2 has not yet answered."""
        raw = self.ledger.evaluate(Fn.LIST_ACCESS_REQUESTS_FOR, target_org=self.org_id)
        return [AccessRequest.model_validate(item) for item in raw]

    def review_access_request(self, request: AccessRequest) -> Decision:
        """Decide whether Org2 will release the data another org has asked for."""
        reasons: list[str] = []
        concerns: list[str] = []

        if request.target_org != self.org_id:
            reasons.append(
                f"request {request.id!r} is addressed to {request.target_org}, not {self.org_id}"
            )

        if request.purpose not in self.permitted_purposes:
            reasons.append(
                f"purpose {request.purpose!r} is not one {self.org_id} releases data for "
                f"automatically; refer to a human reviewer"
            )
        if len(request.justification.strip()) < 20:
            concerns.append("justification is thin enough to be worth a human read")

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

        restricted = [
            str(ref) for ref in request.requested if self._is_restricted(ref)
        ]
        if restricted:
            reasons.append(
                f"compliance-sensitive data is not released on the agent's authority: "
                f"{restricted}; these belong in a negotiated private collection"
            )

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
        """Record Org2's answer as a separate asset in Org2's own namespace."""
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

    def _is_restricted(self, ref) -> bool:
        """Whether a requested asset is compliance-sensitive. An observation
        anchor inherits the restriction of the sensor that produced it, since
        releasing the reading releases the same regulated measurement."""
        if ref.owner_org != self.org_id:
            return False
        if ref.doc_type is DocType.SENSOR:
            sensor = self._own_asset(DocType.SENSOR, ref.asset_id)
        elif ref.doc_type is DocType.OBSERVATION_ANCHOR:
            anchor = self._own_asset(DocType.OBSERVATION_ANCHOR, ref.asset_id)
            sensor = (
                self._own_asset(DocType.SENSOR, anchor["station_id"]) if anchor else None
            )
        else:
            return False
        return sensor is not None and sensor.get("kind") in self.restricted_kinds

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

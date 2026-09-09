"""Org2 Access Negotiation Agent.

Org2 consumes Org1 data (Section 3), and this agent is the *requesting* side of
that exchange: it drafts requests for another organization's data, submits
them, and tracks what Org2 has actually been granted.

It grants nothing. An answer is written by the deciding organization into that
organization's own namespace (Section 12), so all this agent can do is ask and
then read the answer back. `granted_assets` is therefore a *report* of what the
other organizations have released, not a permission Org2 awards itself — the
distinction matters because Org3's audit agent will later check derived work
against records of exactly this kind.

Deterministic. Section 10 lists access negotiation as somewhere a language
model could eventually help — composing a justification, or weighing an unusual
counter-offer — and `draft_request` is where that would attach. The coherence
checks here would still have to pass first.

Private-data arrangements (Section 3's restricted collection for
compliance-sensitive air-quality data) are a Fabric-side construct: membership
of a private data collection is network configuration, not something an agent
writes. This agent negotiates the arrangement; the collection that enforces it
belongs to the network.
"""

from __future__ import annotations

from datetime import datetime

from agent_prototype.agents.base import Agent
from agent_prototype.ledger.port import Fn, TxReceipt
from agent_prototype.shared.models import (
    AccessDecision,
    AccessRequest,
    AccessRequestSubmission,
    AssetRef,
    DocType,
)

__all__ = ["AccessNegotiationAgent", "NegotiationRefused"]


class NegotiationRefused(Exception):
    """The request Org2 was asked to make is incoherent, so it is not sent."""


class AccessNegotiationAgent(Agent):
    # ------------------------------------------------------------------
    # Asking
    # ------------------------------------------------------------------

    def draft_request(
        self,
        request_id: str,
        *,
        target_org: str,
        purpose: str,
        justification: str,
        requested: list[AssetRef],
        valid_until: datetime | None = None,
    ) -> AccessRequestSubmission:
        """Build a request for another organization's data.

        Pure: this touches no ledger. The submission carries no requester
        field — the requester is whoever's identity submits it — so there is
        nothing here for Org2 to misstate about who is asking.
        """
        if target_org == self.org_id:
            raise NegotiationRefused(
                f"{self.org_id} cannot address an access request to itself"
            )

        misdirected = sorted({ref.owner_org for ref in requested if ref.owner_org != target_org})
        if misdirected:
            raise NegotiationRefused(
                f"request is addressed to {target_org} but asks for data owned by "
                f"{', '.join(misdirected)}; each organization answers for its own data"
            )

        submission = AccessRequestSubmission(
            request_id=request_id,
            target_org=target_org,
            purpose=purpose,
            justification=justification,
            requested=requested,
            valid_until=valid_until,
        )
        self.record(
            "draft_request", submission, "DRAFTED",
            request_id=request_id, target_org=target_org, purpose=purpose,
            asset_count=len(requested),
        )
        return submission

    def submit_request(self, submission: AccessRequestSubmission) -> TxReceipt:
        """Propose the request. Whether it commits is the ledger's decision."""
        return self.record_receipt(
            "submit_request",
            submission,
            self.ledger.submit(
                Fn.CREATE_ACCESS_REQUEST, request=submission.model_dump(mode="json")
            ),
        )

    # ------------------------------------------------------------------
    # Reading the answers
    # ------------------------------------------------------------------

    def decisions(self) -> list[AccessDecision]:
        """Every answer any organization has written to an Org2 request."""
        raw = self.ledger.evaluate(Fn.LIST_ACCESS_DECISIONS_FOR, requester_org=self.org_id)
        return [AccessDecision.model_validate(item) for item in raw]

    def outstanding_requests(self) -> list[AccessRequest]:
        """Org2's requests that nobody has answered yet."""
        raw = self.ledger.evaluate(
            Fn.LIST_ASSETS, owner_org=self.org_id, doc_type=str(DocType.ACCESS_REQUEST)
        )
        answered = {decision.request_id for decision in self.decisions()}
        return [
            AccessRequest.model_validate(item) for item in raw if item["id"] not in answered
        ]

    def granted_assets(self) -> list[AssetRef]:
        """What Org2 may currently use, according to the organizations that own
        it. Refusals grant nothing, and an expired approval grants nothing
        either — a grant that has run out is indistinguishable from one that was
        never given.
        """
        now = self.clock.now()
        granted: list[AssetRef] = []
        for decision in self.decisions():
            if not decision.approved:
                continue
            if decision.valid_until is not None and decision.valid_until < now:
                continue
            granted.extend(decision.granted)
        return granted

    def may_use(self, ref: AssetRef) -> bool:
        """Whether Org2 can point at a reason it holds this data legitimately.

        Advisory, like every agent check: it reports what the ledger already
        says. Org2's own data needs no grant, which is why that case is
        answered here rather than looked up.
        """
        return ref.owner_org == self.org_id or ref in self.granted_assets()

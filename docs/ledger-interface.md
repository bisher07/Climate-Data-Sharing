# Ledger interface — the integration contract

This is what the agent layer needs from the chaincode. It is a proposal, not a
description of what exists: it was derived from what Org1's agents actually do,
and from the specification in `claude.md`. Where the deployed chaincode differs,
the chaincode wins — only `src/agent_prototype/ledger/` changes, never the
agents.

The Python form is [`ledger/port.py`](../src/agent_prototype/ledger/port.py).
Everything the agents touch is in that one file.

## The client

```python
class LedgerClient(Protocol):
    @property
    def org_id(self) -> str: ...                       # e.g. "Org1MSP"
    def submit(self, function, /, **arguments) -> TxReceipt: ...
    def evaluate(self, function, /, **arguments) -> Any: ...
    def close(self) -> None: ...
```

Two properties matter more than the signatures.

**The organization is a property of the connection.** `org_id` comes from the
enrolled identity. No agent passes its own organization as an argument
anywhere, so there is no place for an agent to claim to be a different one.

**A rejection is a return value, not an exception.** `submit` answers with a
`TxReceipt` whose status is `COMMITTED` or `REJECTED`. `LedgerError` is
reserved for the network being unreachable or the identity unusable. Agents
depend on that distinction: "the chaincode refused this record" is a normal,
countable outcome, and Section 15's evaluation needs to count them.

`TxReceipt` also carries optional `endorsing_orgs`, `endorsement_seconds` and
`commit_seconds`. Fill them if the gateway exposes them; leave them at their
defaults if not, rather than inventing values.

## Functions

Every write is proposed by an agent of the organization that will own the
result. `owner_org` is never an argument — the chaincode takes it from the
transaction creator's MSP ID.

### Writes

| Function | Argument | Returns |
| --- | --- | --- |
| `RegisterStation` | `request`: `StationRegistrationRequest` | the stored `Station` |
| `RegisterInstrument` | `request`: `InstrumentRegistrationRequest` | the stored `Instrument` |
| `RegisterSensor` | `request`: `SensorRegistrationRequest` | the stored `Sensor` |
| `CreateObservationAnchor` | `request`: `ObservationAnchorRequest` | the stored `ObservationAnchor` |
| `RegisterForecastProduct` | `request`: `ForecastProductRequest` | the stored `ForecastProduct` |
| `CreateQualityRecord` | `request`: `QualityRecordRequest` | the stored `QualityRecord` |
| `CreateDivergenceFlag` | `request`: `DivergenceFlagRequest` | the stored `DivergenceFlag` |
| `CreateAccessRequest` | `request`: `AccessRequestSubmission` | the stored `AccessRequest` |
| `RespondToAccessRequest` | `request`: `AccessDecisionRequest` | the stored `AccessDecision` |

`Station`+`Instrument` and `Sensor` are two shapes of the same idea. Org1 runs
staffed meteorological stations with separately calibrated instruments; Org2
runs a dense low-cost network where the site *is* the device (Section 3), so
its anchors carry the same sensor id in both `station_id` and `instrument_id`.

### Reads

| Function | Arguments | Returns |
| --- | --- | --- |
| `GetAsset` | `owner_org`, `doc_type`, `asset_id` | the asset, or `None` |
| `ListAssets` | `owner_org`, `doc_type` | list of assets |
| `VerifyObservationAnchor` | `owner_org`, `observation_id`, `data_hash` | `{exists, matches, owner_org, reason}` |
| `ListAccessRequestsFor` | `target_org` | unanswered `AccessRequest`s addressed to that org |
| `ListAccessDecisionsFor` | `requester_org` | `AccessDecision`s answering that org's requests |

`ListAccessDecisionsFor` spans namespaces by design. An answer is written by
the deciding organization into *its* namespace, so a requester cannot find the
answers to its own requests by listing its own assets.

Payload shapes are the pydantic models in
[`shared/models/assets.py`](../src/agent_prototype/shared/models/assets.py),
serialised with `model_dump(mode="json")`.

## Rules the chaincode must enforce

The agents deliberately do not enforce these. An agent's checks are advisory and
run before submission; these have to hold even when the agent is wrong,
compromised, or replaced.

1. **Ownership comes from the certificate.** `owner_org` on a stored asset is
   the submitter's MSP ID. Never read it from the request payload — the request
   models have no such field, and that is on purpose.
2. **An organization writes only its own namespace.** Org1 cannot write Org2's
   data, and Org3 cannot claim to own Org1's (Section 12).
3. **Referential integrity within a namespace.** An instrument must be
   registered at a station its own organization registered; an observation
   anchor must reference an active station and an instrument installed at that
   same station, or — for an organization with the combined `Sensor` shape — an
   active sensor its own organization registered.
   A `QualityRecord` must reference an observation anchor **the submitting
   organization owns**: scoring another organization's raw reading is not a
   quality record, it is a divergence flag.
   A `DivergenceFlag` must reference an anchor the submitter owns *and* an
   anchor that genuinely exists in the referenced organization's namespace.
4. **Identifiers are unique per namespace.** Re-registering an existing id is a
   rejection, not an update.
5. **An access decision may only answer a request addressed to the deciding
   organization**, and it is written into the *decider's* namespace. It is a
   separate asset rather than a mutation of the requester's record, because
   neither organization may write into the other's namespace.
6. **Observations cannot be anchored from the future**, beyond a small clock-skew
   allowance.
7. **A divergence flag is evidence, not a verdict.** It records that two
   readings differ, by how much, and against what threshold. Nothing in it
   marks either organization as wrong, and committing one must give its author
   no standing over the referenced organization's data (Section 9).
8. **Only an asset's owner can grant access to it.** An access decision whose
   `granted` list names another organization's asset is rejected — answering a
   request addressed to you does not let you release someone else's data.
9. **Citing another organization's observation needs a current grant.** A
   divergence flag whose reference belongs to another organization is
   rejected unless that organization has an approved, unexpired decision for
   the submitter covering it. A grant covers an observation directly, or
   through the station or sensor that produced it. Comparing two readings means
   holding the other side's raw data, and only its owner can release that.

Stored assets carry `id` and not the request's own identifier field
(`station_id`, `observation_id`, `product_id`…), so every record reads back as
its ledger model with nothing left over.

## Co-endorsement — what the agents contribute

A composite record lives in its proposer's namespace but cites assets in other
namespaces. Before a cited organization's peers endorse it, that
organization's policy agent reviews a `CompositeProposal` through
`review_composite`, and the integration layer should gate the peer's
endorsement on that decision.

The review inspects **citations only**: that each cited asset of the reviewing
organization exists, that the claimed `data_hash` is the one on the ledger, and
that the proposer holds a current grant for it. An organization cited nowhere
in the proposal has no standing and refuses.

`CompositeProposal` has no field describing what the record concludes, and
that is load-bearing. Under the endorsement policy below, Org1 co-endorses
Org2's divergence flags about Org1's own readings. A reviewer shown the
conclusion could withhold endorsement from a record for disagreeing with it —
which would turn co-endorsement into a veto on disagreement. The chaincode must
not reintroduce one either.

## Two things worth deciding together

**Endorsement policy.** Section 2 says Org1 must co-endorse composite records
that reference its stations, and Section 5 requires derived products to prove
their lineage. A useful way to express this is: the submitting organization
endorses, *and so does every organization whose namespace the transaction read*.
In Fabric that is state-based endorsement — the owning org sets a key-level
policy with `SetStateValidationParameter` when it creates a key. A plain
channel-level policy is both too strict for single-org writes and too weak for
lineage.

**Canonical hashing.** `data_hash` is `sha256` over JSON with sorted keys, no
insignificant whitespace and escaped non-ASCII
([`shared/utilities/canonical.py`](../src/agent_prototype/shared/utilities/canonical.py)).
Any component that recomputes a hash — chaincode, another organization's
validator, an auditor — must use exactly this, or lineage and divergence
detection silently stop working.

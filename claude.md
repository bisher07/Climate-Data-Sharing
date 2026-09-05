# CLAUDE.md

## Project Overview

This project is a research prototype for:

**Climate Data Sharing using Hyperledger Fabric + Agentic AI**

The goal is to investigate how a permissioned blockchain and multi-agent architecture can enable trusted, traceable, quality-aware sharing of climate and environmental data between mutually distrusting organizations.

The system consists of **three independent organizations** operating on a Hyperledger Fabric network:

1. **Org1 — Meteorological Authority**
2. **Org2 — Municipal / Environmental Authority**
3. **Org3 — SSCCR / University of Sharjah**

Each organization controls its own identities and permissions.

The architecture must preserve organizational autonomy: **no organization should be able to impersonate another organization, write into another organization's namespace, or approve another organization's data unless explicitly defined by the endorsement policy.**

---

# 1. Core Architecture

The high-level flow is:

```text
                         Hyperledger Fabric Network
 ┌──────────────────────────────────────────────────────────────────┐
 │                                                                  │
 │   Org1                         Org2                         Org3   │
 │   Meteorological              Municipal /                  SSCCR │
 │   Authority                   Environment                   UoS   │
 │                                                                  │
 │   CA + MSP                    CA + MSP                    CA + MSP│
 │   2 Peers                     2 Peers                     2 Peers │
 │   1 Orderer                   1 Orderer                   1 Orderer│
 │                                                                  │
 │   Agents                      Agents                      Agents │
 │     │                            │                           │    │
 │     └───────────────┬────────────┴───────────────┬───────────┘    │
 │                     │                            │                │
 │                     ▼                            ▼                │
 │              Smart Contracts / Chaincode                       │
 │                     │                                            │
 │                     ▼                                            │
 │              Shared Immutable Ledger                             │
 │                                                                  │
 └──────────────────────────────────────────────────────────────────┘
```

Important:

* Each organization operates independently.
* Each organization runs its own CA/MSP.
* Each organization owns its identities.
* Each organization has two peers.
* Each organization has one orderer.
* Do not create a centralized identity authority.
* Do not allow one organization to write directly into another organization's data namespace.
* Endorsement policies must reflect data ownership.

---

# 2. Organizations

## Org1 — Meteorological Authority

### Role

Org1 is the authoritative producer of meteorological observations.

It is primarily a producer and is approximately **near write-only** with respect to its own observations.

### Responsibilities

Org1:

* Registers meteorological stations.
* Registers instruments.
* Records observation anchors.
* Registers forecast products.
* Provides provenance information.
* Endorses its own raw observations.
* Must participate in endorsement of composite records referencing its stations.

### Agents

Org1 hosts:

```text
Ingestion & Provenance Agent
Policy / Endorsement Agent
```

### Ingestion & Provenance Agent

Responsibilities:

* Receive meteorological observations.
* Validate basic schema.
* Generate content hashes.
* Collect provenance metadata.
* Associate observations with stations/instruments.
* Prepare blockchain transactions.
* Propose observation-anchor transactions.

The agent should NOT directly bypass Fabric endorsement rules.

### Policy / Endorsement Agent

This is an Org1-specific agent.

Responsibilities:

* Evaluate requests involving Org1 data.
* Determine whether Org1's data can participate in a proposed transaction.
* Verify that required provenance exists.
* Determine whether Org1 should endorse a transaction.
* Never approve another organization's data on behalf of that organization.

### On-chain data

Org1 can write:

* Station registrations
* Instrument registrations
* Observation anchors
* Observation hash
* Station identifier
* Timestamp
* Calibration metadata
* DOI/URI
* Forecast product registrations

### Reading permissions

Org1 can read:

* Public provenance metadata.
* Access requests addressed to Org1.
* Relevant blockchain state.

### Endorsement rules

Org1:

* Is the sole endorser for its own raw observations.
* Must co-endorse composite records referencing Org1 stations.
* Cannot endorse Org2-owned raw data.
* Cannot endorse Org3-derived products unless the transaction explicitly requires Org1's endorsement because its data is being referenced.

### Private Data Collections

Default:

* No private collection for core observations because they are legally/openly shareable.

Exception:

* Org1 may participate in an Org1–Org3 private collection for embargoed pre-publication forecast products.

---

# 3. Org2 — Municipal / Environmental Authority

## Role

Org2 is both:

* A producer of environmental data.
* A consumer of Org1 data.

Org2 is the primary organization responsible for data-quality validation because it operates the dense low-cost sensor network.

### Agents

Org2 hosts:

```text
Ingestion & Provenance Agent
Quality / Validation Agent
Access Negotiation Agent
Policy / Endorsement Agent
```

### Ingestion & Provenance Agent

Responsibilities:

* Ingest municipal/environmental sensor data.
* Register sensors.
* Generate observation hashes.
* Capture timestamps and provenance.
* Prepare observation-anchor transactions.

### Quality / Validation Agent

This is the primary validation agent in the system.

Responsibilities:

* Validate incoming sensor observations.
* Detect anomalous readings.
* Calculate quality scores.
* Calculate confidence values.
* Compare Org2 measurements against relevant Org1 observations.
* Detect divergence.
* Create divergence flags when appropriate.
* Produce evidence for human review where required.

Important:

The validation agent is **not a neutral third party**.

If Org2's readings conflict with Org1's readings, **Org2's agent writes the divergence flag**.

Do not introduce an artificial neutral validation organization.

### Access Negotiation Agent

Org2 consumes Org1 data.

Responsibilities:

* Handle requests for Org1 data.
* Negotiate access according to policy.
* Request access to data required by Org2.
* Manage negotiated private-data arrangements.

### Policy / Endorsement Agent

Responsibilities:

* Evaluate transactions involving Org2-owned data.
* Decide whether Org2 should endorse.
* Ensure that Org2 does not accidentally endorse data owned by another organization.

### On-chain data

Org2 writes:

* Sensor registrations
* Observation anchors
* Air-quality observations
* Microclimate observations
* Coastal/environmental observations
* Quality scores
* Confidence values
* Divergence flags

### Reading permissions

Org2 can read:

* Org1 forecast products that it has access to.
* Its own data.
* Requests addressed to Org2.
* Public provenance metadata.

### Endorsement rules

Org2:

* Is the sole endorser for its own raw readings.
* Co-endorses composite records involving its data.
* Cannot approve Org1-owned raw data.
* Cannot approve Org3-derived outputs on behalf of Org3.

### Private Data Collections

Org2 can participate in a restricted private data collection for:

* Compliance-sensitive air-quality data.
* Other data that is not covered by the open-exchange obligation.

---

# 4. Org3 — SSCCR / University of Sharjah

## Role

Org3 is primarily a:

* Consumer of data.
* Producer of derived scientific/research outputs.
* Auditor/supervisor.

Org3 does NOT own the original Org1/Org2 observations.

### Agents

Org3 hosts:

```text
Access Negotiation Agent
Audit / Supervisor Agent
Ingestion Agent
```

### Access Negotiation Agent

Responsibilities:

* Submit access requests.
* State purpose and justification.
* Negotiate access to Org1/Org2 data.
* Handle bilateral access agreements.
* Track which data has been legitimately obtained.

### Audit / Supervisor Agent

Responsibilities:

* Monitor agent actions.
* Review proposed actions.
* Verify evidence hashes.
* Check confidence scores.
* Detect policy violations.
* Verify lineage of derived products.
* Provide human-supervision mechanisms.

### Ingestion Agent

Used for Org3's own derived outputs.

Responsibilities:

* Receive generated research products.
* Generate output hashes.
* Construct lineage metadata.
* Reference input data hashes.
* Prepare derived-product registration transactions.

### On-chain data

Org3 writes:

* Access requests.
* Purpose and justification.
* Agent action proposals.
* Evidence hashes.
* Confidence scores.
* Derived product registrations.
* Lineage pointers to Org1/Org2 input hashes.

### Reading permissions

Org3 can read:

* Public provenance metadata.
* Data it successfully negotiated access to.
* Relevant private collections.
* Metadata required to verify lineage.

---

# 5. Critical Org3 Invariant

This is one of the most important rules in the system.

**Org3 cannot self-endorse a derived product if it does not own the underlying input data.**

A derived product may only be accepted if:

1. The input owners' observation anchors already exist on the ledger.
2. The lineage pointers resolve to valid input records.
3. Org3 has legitimately obtained access to the required input data.
4. The derived product identifies its source hashes.
5. The endorsement policy reflects the ownership of the inputs.

Example:

```text
Org1 Observation
       │
       │ hash: ABC123
       ▼
   Blockchain
       │
       │
Org2 Observation
       │
       │ hash: XYZ789
       ▼
   Blockchain
       │
       ├───────────────┐
       │               │
       ▼               ▼
   Org3 receives   Org3 receives
   authorized      authorized
   data            data
       │               │
       └───────┬───────┘
               ▼
        Org3 Research Agent
               │
               ▼
        Derived Product
               │
       lineage:
       ABC123 + XYZ789
               │
               ▼
          Blockchain
```

The smart contract must verify that the referenced input records exist.

Org3 must not be able to simply claim:

```text
"I used Org1's data."
```

without a valid on-chain lineage reference.

---

# 6. Fabric Network Requirements

Each organization should have:

```text
Org CA
Org MSP
2 Peers
1 Orderer
```

The system should use a permissioned Hyperledger Fabric network.

Use Fabric identities and MSPs to enforce organization-level authorization.

The implementation should clearly separate:

```text
Identity
Authorization
Endorsement
Data ownership
Data storage
Agent decision-making
```

Do not implement authorization only inside the AI agents.

The blockchain layer must enforce critical authorization rules independently.

---

# 7. Smart Contract / Chaincode Design

Use chaincode to represent the domain objects.

Suggested assets:

```text
Station
Instrument
Sensor
ObservationAnchor
ForecastProduct
QualityRecord
DivergenceFlag
AccessRequest
AgentActionProposal
DerivedProduct
AccessAgreement
```

Each asset should contain enough metadata to establish:

* Owner organization
* Creation timestamp
* Asset identifier
* Data hash
* Provenance
* Status
* Relevant organization
* Relevant references

---

# 8. Observation Anchor

An observation should generally NOT store the complete raw dataset directly on the ledger.

Instead store an anchor such as:

```text
ObservationAnchor {
    id
    ownerOrg
    stationId
    instrumentId
    timestamp
    dataHash
    calibrationMetadata
    uri
    doi
    qualityStatus
}
```

The blockchain provides:

* Integrity
* Provenance
* Timestamping
* Ownership
* Verification

Large raw datasets should remain off-chain where appropriate.

---

# 9. Quality / Validation Model

Org2's Quality/Validation Agent should produce records such as:

```text
QualityRecord {
    observationId
    qualityScore
    confidence
    validationMethod
    timestamp
    agentId
}
```

If a significant disagreement is detected:

```text
DivergenceFlag {
    id
    org2ObservationId
    referenceObservationId
    referenceOrg
    divergenceMetric
    threshold
    evidenceHash
    timestamp
}
```

Do not automatically assume that disagreement means one organization is wrong.

The purpose is to make disagreement:

* visible
* traceable
* auditable

---

# 10. Agentic AI Architecture

The system should be genuinely multi-agent.

Agents should have:

* Clearly defined responsibilities.
* Tools they are allowed to use.
* Organization-specific permissions.
* State/context.
* Decision-making capability where appropriate.
* Audit trails.

However:

**Do NOT force every agent to use an LLM.**

Use deterministic logic where deterministic logic is more appropriate.

Examples:

```text
Schema validation          → deterministic
Hash generation            → deterministic
Cryptographic verification → deterministic
Range checking             → deterministic
Blockchain transaction     → deterministic

Complex interpretation     → LLM/AI
Reasoning over evidence    → LLM/AI
Tool selection             → LLM/AI
Access negotiation         → potentially LLM/AI
Anomaly explanation        → potentially LLM/AI
Agent coordination         → potentially LLM/AI
```

The goal is an agentic architecture, not "LLM everywhere."

---

# 11. Agent Workflow

A typical Org2 ingestion workflow:

```text
Sensor Data
    ↓
Ingestion Agent
    ↓
Schema / Basic Validation
    ↓
Quality / Validation Agent
    ↓
 ┌───────────────┐
 │               │
Valid          Suspicious
 │               │
 ↓               ↓
Quality       Divergence /
Score         Review Flag
 │               │
 └───────┬───────┘
         ↓
Policy / Endorsement
         ↓
Fabric Transaction
         ↓
Blockchain Ledger
```

Org3 research workflow:

```text
Research Request
      ↓
Access Negotiation Agent
      ↓
Access Request
      ↓
Organization Approval
      ↓
Authorized Data Access
      ↓
Research / Analysis Agent
      ↓
Derived Product
      ↓
Lineage Construction
      ↓
Verify Input Hashes
      ↓
Fabric Transaction
      ↓
Derived Product Registered
```

---

# 12. Security Principles

The implementation must enforce:

### Organization isolation

Org1 cannot write Org2's data.

Org2 cannot write Org1's data.

Org3 cannot pretend to own Org1/Org2 data.

### Identity isolation

Each organization controls its own identities.

### Endorsement isolation

Only the appropriate organization can endorse its own data.

### Lineage verification

Derived products must reference valid input records.

### Auditability

Important agent actions should produce auditable evidence.

### No trust in the agent itself

AI agents must not be considered trusted authorities.

The blockchain and Fabric endorsement policies remain the enforcement layer.

---

# 13. Project Structure

Prefer a modular repository structure similar to:

```text
project/
│
├── blockchain/
│   ├── network/
│   ├── organizations/
│   │   ├── org1/
│   │   ├── org2/
│   │   └── org3/
│   ├── chaincode/
│   └── scripts/
│
├── agents/
│   ├── org1/
│   │   ├── ingestion_provenance/
│   │   └── policy_endorsement/
│   │
│   ├── org2/
│   │   ├── ingestion_provenance/
│   │   ├── quality_validation/
│   │   ├── access_negotiation/
│   │   └── policy_endorsement/
│   │
│   └── org3/
│       ├── access_negotiation/
│       ├── audit_supervisor/
│       └── ingestion/
│
├── shared/
│   ├── schemas/
│   ├── models/
│   └── utilities/
│
├── data/
│   ├── org1/
│   ├── org2/
│   └── org3/
│
├── experiments/
│
├── evaluation/
│
└── docs/
```

The exact structure may be changed if there is a strong technical reason.

---

# 14. Development Philosophy

This is a **research prototype**, not a production climate-data platform.

Prioritize:

1. Correctness of the architecture.
2. Clear organization boundaries.
3. Reproducibility.
4. Explainability.
5. Auditability.
6. Experimental evaluation.
7. Simple implementations over unnecessary complexity.

Do not over-engineer features that are not relevant to the research questions.

---

# 15. Research Evaluation

The system should eventually support experiments comparing:

### Baseline 1

Centralized climate-data sharing.

### Baseline 2

Blockchain-only climate-data sharing.

### Baseline 3

AI-assisted validation without blockchain.

### Proposed system

Blockchain + multi-agent architecture.

Possible metrics:

```text
Data validation accuracy
Anomaly detection accuracy
False positive rate
False negative rate
Data provenance completeness
Verification time
Transaction latency
Endorsement latency
Agent execution success rate
Access negotiation time
Blockchain storage overhead
Number of invalid/suspicious records detected
Lineage verification success
```

The implementation should make it possible to collect these metrics rather than only demonstrating that the system works.

---

# 16. Important Design Principle

Do not make the architecture:

```text
             LLM
              ↓
         Blockchain
              ↓
           Data
```

The intended architecture is:

```text
                  Organization
                       │
                ┌──────┴──────┐
                │             │
             Agents       Organization
                │          Policies
                │             │
                └──────┬──────┘
                       ↓
                 Fabric Client
                       ↓
                Endorsement
                       ↓
                 Smart Contract
                       ↓
                    Ledger
```

AI agents propose and reason.

**Fabric enforces.**

This distinction is fundamental.

---

# 17. What Claude Should Build First

Implement incrementally.

### Phase 1 — Fabric network

Build:

* Org1
* Org2
* Org3
* CAs/MSPs
* 2 peers per organization
* 1 orderer per organization
* Channels/private data architecture as required
* Basic identities

### Phase 2 — Chaincode

Implement:

* Station
* Sensor
* ObservationAnchor
* ForecastProduct
* QualityRecord
* DivergenceFlag
* AccessRequest
* DerivedProduct

Implement ownership and authorization rules.

### Phase 3 — Basic application layer

Allow organizations to:

* Register data.
* Create observation anchors.
* Query provenance.
* Create access requests.
* Register derived products.

### Phase 4 — Agents

Implement agents incrementally.

Start with deterministic agents and workflows.

Then introduce LLM-based reasoning only where it provides meaningful value.

### Phase 5 — Agent ↔ Fabric integration

Agents should interact with Fabric through controlled tools/functions.

Agents should NOT have unrestricted access to the blockchain.

### Phase 6 — End-to-end scenarios

Implement at minimum:

#### Scenario A — Org1 observation

```text
Org1
→ Ingestion Agent
→ Observation Anchor
→ Org1 endorsement
→ Ledger
```

#### Scenario B — Org2 validation

```text
Org2
→ Sensor observation
→ Validation Agent
→ Quality Score
→ Ledger
```

#### Scenario C — Divergence

```text
Org1 observation
       +
Org2 observation
       ↓
Org2 Validation Agent
       ↓
Divergence detected
       ↓
Divergence Flag
       ↓
Ledger
```

#### Scenario D — Org3 research

```text
Org3
→ Access Request
→ Org1/Org2 authorization
→ Data access
→ Research Agent
→ Derived Product
→ Input lineage verification
→ Ledger
```

#### Scenario E — Invalid lineage

Test that Org3 cannot register:

```text
DerivedProduct(
    inputHash = fake_hash
)
```

if the input hash does not exist on the ledger.

This negative test is essential.

---

# 18. Coding Rules

Before implementing a feature:

1. Understand which organization owns the data.
2. Determine which agent is responsible.
3. Determine which Fabric identity is used.
4. Determine who must endorse the transaction.
5. Determine whether the data is public or private.
6. Determine whether the operation should be recorded on-chain.
7. Add tests for both valid and invalid authorization.

Never silently weaken endorsement or authorization rules to make a demo work.

If a Fabric configuration is technically difficult, explain the issue and propose an alternative rather than bypassing the security model.

---

# 19. Avoid These Mistakes

Do NOT:

* Create one global CA for all organizations.
* Give Org3 ownership of Org1/Org2 raw data.
* Allow any organization to endorse another organization's raw observations.
* Store large raw climate datasets directly on-chain unnecessarily.
* Treat the LLM as the security mechanism.
* Allow an agent to bypass Fabric endorsement policies.
* Automatically treat divergence as proof that an organization is incorrect.
* Allow Org3 to create derived products without verifiable input lineage.
* Make every agent an LLM agent unnecessarily.
* Build UI before validating the underlying architecture.
* Add technologies simply because they are popular.

---

# 20. When Making Architectural Decisions

Always ask:

> **Does this decision strengthen the research hypothesis and preserve the organizational trust model?**

If not, avoid it unless there is a strong technical reason.

The central research concept is:

```text
Trusted climate-data sharing
        +
Data quality / validation
        +
Data provenance
        +
Organizational autonomy
        +
Agentic decision-making
        +
Blockchain-enforced trust
```

The implementation should demonstrate how these components work together rather than treating blockchain and AI as unrelated technologies.

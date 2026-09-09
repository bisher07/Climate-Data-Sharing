# Climate Data Sharing — agent layer

Research prototype. Architecture specification: [claude.md](claude.md).

This repository holds the **agent layer only**. The Hyperledger Fabric network
and its chaincode are built separately; the two are joined at one seam, the
`LedgerClient` interface described in
[docs/ledger-interface.md](docs/ledger-interface.md).

## Status

| | |
| --- | --- |
| Ledger interface (the integration seam) | done |
| In-memory stand-in ledger, for developing agents | done |
| Org1 — Ingestion & Provenance, Policy / Endorsement | done |
| Org1 ingestion pipeline (Scenario A) | done |
| Org2 — Ingestion, Quality / Validation, Access Negotiation, Policy | done |
| Org2 validation pipeline (Scenarios B and C) | done |
| Org3 — three agents | not started |
| Wiring to the real Fabric gateway | not started |

## Run

```bash
uv run python experiments/scenario_a_org1.py   # Org1, narrated
uv run python experiments/scenario_b_org2.py   # Org2 + cross-org divergence
uv run pytest                                  # 87 tests
```

## Layout

```
src/agent_prototype/
├── ledger/          the interface agents talk to, plus an in-memory stand-in
├── shared/          domain models, canonical hashing, injectable clock
├── agents/org1/     Org1's two agents and its ingestion pipeline
└── agents/org2/     Org2's four agents and its validation pipeline
tests/               agent behaviour: screening, refusals, audit trail
experiments/         runnable scenarios
docs/                the integration contract
```

Org1 and Org2 share the models and the ledger interface, and nothing else. They
do not import each other: in the deployed system these are separate codebases
run by separate organizations, so the resemblance between their policy agents
is deliberate duplication rather than a missing abstraction.

## How the seam works

Agents are constructed with a `LedgerClient` and nothing else:

```python
ledger  = InMemoryLedger("Org1MSP")          # ← swap for the Fabric-backed client
agent   = IngestionProvenanceAgent("org1.ingestion", ledger)
```

Integration means writing one class that implements `LedgerClient` against the
real Fabric gateway. No agent code changes.

`InMemoryLedger` is a development stand-in with **no security properties** — no
identities, no signatures, no endorsement, no consensus. It enforces ownership
and referential integrity only so that agents meet realistic refusals while
being developed. Nothing proved against it says anything about the security of
the system.

## The principle

> AI agents propose and reason. **Fabric enforces.**

Agent checks run before submission and are advisory: they can stop an
organization from proposing something, but they grant nothing. Every rule that
must hold even when an agent is wrong belongs to the chaincode, and
[docs/ledger-interface.md](docs/ledger-interface.md) lists those explicitly.

Org2's validation agent is the sharpest case. It can score its own readings and
flag disagreement with Org1's, but a divergence flag is written into Org2's
namespace and says only *these two readings differ, by this much* — never that
Org1 is wrong. Section 9 is explicit that Org2 is not a neutral third party, so
the agent makes disagreement visible and leaves adjudication to people.

Every agent so far is deterministic — schema validation, hashing, scoring,
referential checks — which is what Section 10 asks for. There is no LLM in this
layer yet.

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
| Org1 — Ingestion & Provenance Agent | done |
| Org1 — Policy / Endorsement Agent | done |
| Org1 ingestion pipeline (Scenario A) | done |
| Org2 — four agents | not started |
| Org3 — three agents | not started |
| Wiring to the real Fabric gateway | not started |

## Run

```bash
uv run python experiments/scenario_a_org1.py   # narrated walkthrough
uv run pytest                                  # 37 tests
```

## Layout

```
src/agent_prototype/
├── ledger/          the interface agents talk to, plus an in-memory stand-in
├── shared/          domain models, canonical hashing, injectable clock
└── agents/org1/     Org1's two agents and its ingestion pipeline
tests/               agent behaviour: screening, refusals, audit trail
experiments/         runnable scenarios
docs/                the integration contract
```

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

Agent checks run before submission and are advisory: they can stop Org1 from
proposing something, but they grant nothing. Every rule that must hold even when
an agent is wrong belongs to the chaincode, and
[docs/ledger-interface.md](docs/ledger-interface.md) lists those explicitly.
Org1's agents are deterministic throughout — schema validation, hashing,
referential checks — which is what Section 10 asks for. There is no LLM in this
layer yet.

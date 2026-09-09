# Reasoning interface — where a language model attaches

This project has two seams. [`ledger/port.py`](../src/agent_prototype/ledger/port.py)
is where agents meet the blockchain;
[`reasoning/port.py`](../src/agent_prototype/reasoning/port.py) is where they
meet a language model. They are the same shape on purpose: a narrow Protocol, a
frozen result type, and failure as a value rather than an exception.

Nothing in the agent layer uses an LLM yet. This is the contract the
LLM-backed agents will be written against, defined first so that the properties
below are load-bearing from the start rather than retrofitted.

## The client

```python
class Advisor(Protocol):
    @property
    def model(self) -> str: ...                       # e.g. "claude-sonnet-5"
    def advise(self, request: AdvisoryRequest) -> Advice: ...
```

An agent takes one as an optional keyword:

```python
agent = QualityValidationAgent("org2.quality", ledger)                  # deterministic
agent = QualityValidationAgent("org2.quality", ledger, advisor=claude)  # + reasoning
```

## Why an LLM is allowed near a decision at all

Section 19 lists *"treat the LLM as the security mechanism"* as a mistake to
avoid, and Section 16 says agents propose while Fabric enforces. Four
properties make that structural rather than a convention someone remembers.

### 1. Advice cannot approve

`Advice` has no `approved` field, exactly as `AccessRequestSubmission` has no
`requester_org` field. There is nowhere for a model to put an approval, so no
prompt injection, model error or misuse can produce one.

```python
@dataclass(frozen=True, slots=True)
class Advice:
    concerns: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    explanation: str | None = None
    model: str | None = None
    available: bool = True
```

### 2. Advice only ever tightens

`Decision.tightened_by(advice)` is the only way advice enters a decision:

| | deterministic decision | after advice |
| --- | --- | --- |
| approved, advice adds a concern | approved | approved, with the concern |
| approved, advice adds a reason | approved | **refused** |
| refused, advice says it is fine | refused | **refused** |
| refused, advice adds nothing | refused | refused, reason intact |

A model that is wrong — or one talked into something by hostile text sitting in
an access request's `justification` field — can only make an agent *more*
cautious. That is the safe direction for the failure to run in, and it is why
the fold lives in `Decision` rather than in each agent, where one of them would
eventually get it wrong.

### 3. A model is optional and may fail

An agent with no advisor behaves exactly as it does today. An advisor that
errors or times out degrades to `Advice.unavailable()`, which contributes
nothing in either direction. Ingestion must not stop because a language model
is down, so the deterministic path is always the whole path *plus*, never the
whole path *minus*.

`AdvisorError` is raised by an advisor and caught by `Agent.consult`; agents
never handle it themselves.

### 4. Every consultation is auditable

`Agent.consult` writes an `AuditRecord` whose `input_hash` is the hash of
exactly what was disclosed, together with the model id. A reviewer can prove
what the model was, and was not, shown — which is what Section 12's "important
agent actions should produce auditable evidence" requires of a component whose
output is not reproducible.

## Disclosure is explicit

`AdvisoryRequest.disclosed` is the whole of what the model sees, and the
calling agent builds it field by field.

```python
advice = self.consult(AdvisoryRequest(
    task="explain-divergence",
    question="Why might these two readings disagree?",
    disclosed={"variable": "air_temperature", "org2_value": 27.9, "org1_value": 19.4},
))
```

**Never pass a raw `ObservationRecord`.** Section 8 keeps raw data off-chain,
and a hosted model is no more on-premise than a ledger is. Org2's
compliance-sensitive air-quality readings (Section 3) must not reach a
third-party API because an agent wanted a sentence of prose. The audit trail
records the disclosure hash precisely so this rule can be checked after the
fact rather than trusted.

## Determinism

Section 14 asks for replayable experiment runs, and a live model is not
replayable. [`reasoning/stub.py`](../src/agent_prototype/reasoning/stub.py)
provides `ScriptedAdvisor` (fixed advice per task, records what it was asked)
and `FailingAdvisor` (always unreachable). The same agent code runs against a
scripted advisor in tests and a real one in deployment, the way the same agent
code runs against `InMemoryLedger` and a Fabric gateway.

## Where this gets used

Per Section 10's table, and in the order the data becomes available:

| Agent | Task | What the model adds |
| --- | --- | --- |
| Org2 Quality / Validation | `explain-divergence` | The human-review prose accompanying a divergence flag (Section 3: "produce evidence for human review"). The metric and threshold stay deterministic. |
| Org2 / Org3 Access Negotiation | `assess-justification` | Reading a free-text justification, instead of matching `purpose` against a fixed set. It can refuse or flag; it cannot grant. |
| Org3 Audit / Supervisor | `review-evidence` | Reasoning over lineage and evidence to spot policy violations — the spec's strongest case, pending Org3. |

Everything else stays deterministic. Section 10's instruction is explicit:
*"Do NOT force every agent to use an LLM."*

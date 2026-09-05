"""Org1's agents, narrated.

Runs the ingestion pipeline against the in-memory stand-in ledger, so it shows
what the *agents* do. It proves nothing about blockchain enforcement — that
lives in the Fabric network and its chaincode.

    uv run python experiments/scenario_a_org1.py
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_prototype.agents.org1 import (
    IngestionProvenanceAgent,
    Org1IngestionPipeline,
    PolicyEndorsementAgent,
)
from agent_prototype.ledger import Fn, InMemoryLedger
from agent_prototype.shared.models import (
    AccessRequestSubmission,
    AssetRef,
    Calibration,
    DocType,
    InstrumentRegistrationRequest,
    ObservationRecord,
    StationRegistrationRequest,
    Variable,
)
from agent_prototype.shared.utilities import FixedClock

ORG1, ORG3 = "Org1MSP", "Org3MSP"
T0 = datetime(2026, 1, 15, tzinfo=UTC)


def rule(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m\n" + "─" * 74)


def main() -> None:
    clock = FixedClock(T0, step=timedelta(seconds=1))
    ledger = InMemoryLedger(ORG1, clock=clock)

    ingestion = IngestionProvenanceAgent("org1.ingestion", ledger, clock=clock)
    policy = PolicyEndorsementAgent("org1.policy", ledger, clock=clock)
    pipeline = Org1IngestionPipeline(ingestion, policy)

    rule("Org1 — Meteorological Authority")
    print(f"  org1.ingestion   prepares transactions — schema, plausibility, hash, provenance")
    print(f"  org1.policy      decides what Org1 will stand behind")
    print(f"  ledger           {type(ledger).__name__} (stand-in; no signatures, no consensus)")

    calibration = Calibration(
        calibrated_at=datetime(2025, 12, 1, tzinfo=UTC),
        calibrated_by="NMI-UAE", procedure="ISO-17025", uncertainty=0.15, unit="degC",
    )

    rule("1. Register a station and an instrument")
    r = ingestion.register_station(StationRegistrationRequest(
        station_id="ST-SHJ-001", name="Sharjah Coastal AWS",
        latitude=25.3463, longitude=55.4209, elevation_m=8.0, wmo_id="41556",
    ))
    print(f"  RegisterStation      {r.status}")
    r = ingestion.register_instrument(InstrumentRegistrationRequest(
        instrument_id="IN-THERM-014", station_id="ST-SHJ-001", kind="thermometer",
        model="Vaisala HMP155", serial_number="J1940123", calibration=calibration,
    ))
    print(f"  RegisterInstrument   {r.status}")

    rule("2. A good observation")
    record = ObservationRecord(
        station_id="ST-SHJ-001", instrument_id="IN-THERM-014",
        phenomenon_time=datetime(2026, 1, 14, 12, 0, tzinfo=UTC),
        variables={"air_temperature": Variable(value=27.4, unit="degC"),
                   "relative_humidity": Variable(value=62.0, unit="percent")},
        source_system="org1-aws-telemetry",
    )
    print(f"  raw reading      {{'air_temperature': 27.4degC, 'relative_humidity': 62.0%}}")
    result = pipeline.ingest("OBS-2026-01-14-1200", record, calibration)
    print(f"  screening        passed")
    print(f"  policy review    {result.decision.summary}")
    print(f"  ledger           {result.receipt.status}  →  stage {result.stage}")
    print(f"  anchored hash    {result.prepared.data_hash}")
    print(f"  raw measurements never left the agent — only the hash was submitted")

    rule("3. A broken observation")
    absurd = record.model_copy(
        update={"variables": {"air_temperature": Variable(value=412.0, unit="degC")}}
    )
    result = pipeline.ingest("OBS-BAD", absurd, calibration)
    print(f"  stage            {result.stage}")
    print(f"  reason           {result.error}")
    print(f"  nothing was submitted to the ledger")

    rule("4. An anchor that misdescribes its data")
    prepared = ingestion.prepare_anchor("OBS-SWAP", record, calibration)
    swapped = type(prepared)(
        request=prepared.request,
        record=record.model_copy(update={"variables": {
            **record.variables, "air_temperature": Variable(value=99.0, unit="degC")}}),
        data_hash=prepared.data_hash,
    )
    print(f"  policy review    {policy.review_anchor(swapped).summary}")

    rule("5. Integrity check")
    print(f"  original reading  matches={ingestion.verify_anchor('OBS-2026-01-14-1200', record)['matches']}")
    tampered = record.model_copy(update={"variables": {
        **record.variables, "air_temperature": Variable(value=21.0, unit="degC")}})
    print(f"  altered by 6.4C   matches={ingestion.verify_anchor('OBS-2026-01-14-1200', tampered)['matches']}")

    rule("6. Org3 asks for access to Org1 data")
    org3 = ledger.connect_as(ORG3)
    for request_id, purpose, justification in [
        ("AR-001", "research", "Urban heat island study across the Sharjah coastal strip."),
        ("AR-002", "commercial-resale", "Redistribution to subscribers."),
    ]:
        org3.submit(Fn.CREATE_ACCESS_REQUEST, request=AccessRequestSubmission(
            request_id=request_id, target_org=ORG1, purpose=purpose,
            justification=justification,
            requested=[AssetRef(owner_org=ORG1, doc_type=DocType.STATION, asset_id="ST-SHJ-001")],
        ).model_dump(mode="json"))

    for pending in policy.pending_access_requests():
        decision = policy.review_access_request(pending)
        receipt = policy.respond_to_access_request(pending, decision)
        granted = receipt.payload["granted"]
        print(f"  {pending.id}  purpose={pending.purpose:18} → "
              f"{'APPROVED' if decision.approved else 'REFUSED '}  granted={len(granted)}")
        if not decision.approved:
            print(f"          {decision.reasons[0]}")

    rule("7. Org1 is asked to speak for Org2")
    decision = policy.review_foreign_data("Org2MSP", "an Org2 air-quality reading")
    print(f"  {decision.reasons[0]}")

    rule("8. Audit trail")
    for entry in (ingestion.audit_trail + policy.audit_trail)[:12]:
        print(f"  {entry.timestamp:%H:%M:%S}  {entry.agent_id:15} {entry.action:26} {entry.outcome}")


if __name__ == "__main__":
    main()

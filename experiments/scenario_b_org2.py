"""Org2's agents, narrated — Scenarios B and C of Section 6.

Runs Org2's validation pipeline against the in-memory stand-in ledger, so it
shows what the *agents* do. It proves nothing about blockchain enforcement —
that lives in the Fabric network and its chaincode.

    uv run python experiments/scenario_b_org2.py
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agent_prototype.agents.org1 import IngestionProvenanceAgent as Org1Ingestion
from agent_prototype.agents.org1 import PolicyEndorsementAgent as Org1Policy
from agent_prototype.agents.org2 import (
    AccessNegotiationAgent,
    IngestionProvenanceAgent,
    Org2ValidationPipeline,
    PolicyEndorsementAgent,
    QualityValidationAgent,
)
from agent_prototype.ledger import Fn, InMemoryLedger
from agent_prototype.shared.models import (
    AccessRequestSubmission,
    AssetRef,
    Calibration,
    CitedInput,
    CompositeProposal,
    DocType,
    InstrumentRegistrationRequest,
    ObservationRecord,
    SensorRegistrationRequest,
    StationRegistrationRequest,
    Variable,
)
from agent_prototype.shared.utilities import FixedClock

ORG1, ORG2 = "Org1MSP", "Org2MSP"
T0 = datetime(2026, 1, 15, tzinfo=UTC)
PHENOMENON_TIME = datetime(2026, 1, 14, 12, 0, tzinfo=UTC)


def rule(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m\n" + "─" * 74)


def main() -> None:
    clock = FixedClock(T0, step=timedelta(seconds=1))
    org1_ledger = InMemoryLedger(ORG1, clock=clock)
    org2_ledger = org1_ledger.connect_as(ORG2)

    ingestion = IngestionProvenanceAgent(
        "org2.ingestion", org2_ledger, clock=clock)
    quality = QualityValidationAgent("org2.quality", org2_ledger, clock=clock)
    policy = PolicyEndorsementAgent("org2.policy", org2_ledger, clock=clock)
    negotiation = AccessNegotiationAgent(
        "org2.negotiation", org2_ledger, clock=clock)
    pipeline = Org2ValidationPipeline(ingestion, quality, policy, negotiation)

    rule("Org2 — Municipal / Environmental Authority")
    print("  org2.ingestion    prepares transactions — schema, plausibility, hash, provenance")
    print("  org2.quality      scores readings and makes disagreement visible")
    print("  org2.policy       decides what Org2 will stand behind")
    print("  org2.negotiation  asks other organizations for data, tracks what was granted")

    calibration = Calibration(
        calibrated_at=datetime(2025, 12, 1, tzinfo=UTC),
        calibrated_by="NMI-UAE", procedure="ISO-17025", uncertainty=0.5, unit="degC",
    )

    rule("1. Register two sensors")
    for sensor_id, name, kind in [
        ("SN-MC-014", "Al Majaz microclimate node", "microclimate"),
        ("SN-AQ-031", "Industrial Area air-quality node", "air-quality"),
    ]:
        r = ingestion.register_sensor(SensorRegistrationRequest(
            sensor_id=sensor_id, name=name, latitude=25.3241, longitude=55.3869,
            elevation_m=5.0, kind=kind, model="Clarity Node-S",
            serial_number=f"CN-{sensor_id[-3:]}", calibration=calibration,
        ))
        print(f"  RegisterSensor   {sensor_id}  kind={kind:14} {r.status}")

    rule("2. Scenario B — an Org2 reading is anchored and scored")
    record = ObservationRecord(
        station_id="SN-MC-014", instrument_id="SN-MC-014",
        phenomenon_time=PHENOMENON_TIME,
        variables={"air_temperature": Variable(value=27.9, unit="degC"),
                   "pm2_5": Variable(value=18.0, unit="ug/m3")},
        source_system="org2-sensor-gateway",
    )
    print(
        "  raw reading      {'air_temperature': 27.9degC, 'pm2_5': 18.0ug/m3}")
    result = pipeline.ingest("OBS-ORG2-1200", record, calibration)
    print(f"  screening        passed")
    print(f"  quality score    {result.quality.quality_score:.3f}  "
          f"confidence {result.quality.confidence:.3f}")
    print(f"  policy review    {result.decision.summary}")
    print(f"  anchor           {result.receipt.status}")
    print(f"  quality record   {result.quality_receipt.status}")
    print("  the raw measurements never left the agent — only the hash was submitted")

    rule("3. A physically impossible reading")
    absurd = record.model_copy(
        update={"variables": {"pm2_5": Variable(value=9000.0, unit="ug/m3")}}
    )
    stopped = pipeline.ingest("OBS-BAD", absurd, calibration)
    print(f"  stage            {stopped.stage}")
    print(f"  reason           {stopped.error}")
    print("  nothing was submitted to the ledger")

    rule("4. A poor reading is published, not suppressed")
    extreme = record.model_copy(
        update={"variables": {
            "air_temperature": Variable(value=58.0, unit="degC")}}
    )
    hot = pipeline.ingest("OBS-EXTREME", extreme, calibration)
    print(
        f"  quality score    {hot.quality.quality_score:.3f}  (low, but plausible)")
    print(f"  stage            {hot.stage}")
    print("  Section 9: make doubt visible rather than suppress the record it attaches to")

    rule("5. Scenario C — Org1 and Org2 disagree")
    org1_ingestion = Org1Ingestion("org1.ingestion", org1_ledger, clock=clock)
    org1_ingestion.register_station(StationRegistrationRequest(
        station_id="ST-SHJ-001", name="Sharjah Coastal AWS",
        latitude=25.3463, longitude=55.4209, elevation_m=8.0, wmo_id="41556",
    ))
    org1_ingestion.register_instrument(InstrumentRegistrationRequest(
        instrument_id="IN-THERM-014", station_id="ST-SHJ-001", kind="thermometer",
        model="Vaisala HMP155", serial_number="J1940123", calibration=calibration,
    ))
    org1_record = ObservationRecord(
        station_id="ST-SHJ-001", instrument_id="IN-THERM-014",
        phenomenon_time=PHENOMENON_TIME,
        variables={"air_temperature": Variable(value=19.4, unit="degC")},
        source_system="org1-aws-telemetry",
    )
    org1_anchor = org1_ingestion.prepare_anchor("OBS-ORG1-1200", org1_record, calibration)
    org1_ingestion.submit_anchor(org1_anchor)
    print("  Org1 says        19.4degC        (ST-SHJ-001)")
    print("  Org2 says        27.9degC        (SN-MC-014)")

    blocked = pipeline.compare(
        "OBS-ORG2-1200", record,
        reference_org=ORG1, reference_observation_id="OBS-ORG1-1200",
        reference_record=org1_record, variable="air_temperature", threshold=2.0,
    )
    print(f"  stage            {blocked.stage}")
    print(f"  reason           {blocked.error}")
    print("  Org1 never released that reading to Org2, so the comparison is not run at all")

    rule("6. Org2 asks Org1 for the station's data")
    org1_policy = Org1Policy("org1.policy", org1_ledger, clock=clock)
    station = AssetRef(owner_org=ORG1, doc_type=DocType.STATION, asset_id="ST-SHJ-001")
    reading = AssetRef(
        owner_org=ORG1, doc_type=DocType.OBSERVATION_ANCHOR, asset_id="OBS-ORG1-1200")
    negotiation.submit_request(negotiation.draft_request(
        "AR-ORG2-1", target_org=ORG1, purpose="validation",
        justification="Cross-checking Org2 microclimate nodes against the coastal AWS.",
        requested=[station],
    ))
    print(f"  outstanding      {[r.id for r in negotiation.outstanding_requests()]}")
    pending = org1_policy.pending_access_requests()[0]
    org1_policy.respond_to_access_request(pending, org1_policy.review_access_request(pending))
    print("  Org1 answers     approved — recorded in Org1's namespace")
    print(f"  Org2 may use     {negotiation.may_use(reading)}  "
          f"— a station grant covers the readings it produced")

    rule("7. Scenario C — the comparison, now legitimate")
    diverged = pipeline.compare(
        "OBS-ORG2-1200", record,
        reference_org=ORG1, reference_observation_id="OBS-ORG1-1200",
        reference_record=org1_record, variable="air_temperature", threshold=2.0,
    )
    print(f"  divergence       {diverged.divergence.divergence_metric:.1f}degC "
          f"against a {diverged.divergence.threshold}degC threshold")
    print(f"  policy review    {diverged.decision.summary}")
    print(f"  ledger           {diverged.receipt.status}")
    print(f"  flag owned by    {diverged.receipt.payload['owner_org']}  "
          f"— Org2 writes it, in Org2's namespace")
    endorsement = org1_policy.review_composite(CompositeProposal(
        proposal_id=diverged.divergence.flag_id, proposer_org=ORG2,
        record_kind="divergence_flag",
        cited=[CitedInput(ref=reading, data_hash=org1_anchor.data_hash)],
    ))
    print(f"  Org1 co-endorses {endorsement.summary}")
    print("  Org1 checked the citation — that the reading exists, the hash matches, and Org2")
    print("  was granted it. It was never shown what the flag concludes, so it cannot refuse")
    print("  to endorse a record for disagreeing with it.")

    rule("8. A reading Org1 and Org2 agree on")
    agreed = pipeline.compare(
        "OBS-ORG2-1200", record,
        reference_org=ORG1, reference_observation_id="OBS-ORG1-1200",
        reference_record=record.model_copy(update={
            "variables": {"air_temperature": Variable(value=28.4, unit="degC")}}),
        variable="air_temperature", threshold=2.0,
    )
    print(f"  stage            {agreed.stage}  — nothing to flag, nothing submitted")

    rule("9. Org2 is asked for compliance-sensitive data")
    org1_ledger.submit(Fn.CREATE_ACCESS_REQUEST, request=AccessRequestSubmission(
        request_id="AR-ORG1-9", target_org=ORG2, purpose="research",
        justification="Industrial emissions study for the northern corridor.",
        requested=[
            AssetRef(owner_org=ORG2, doc_type=DocType.SENSOR, asset_id="SN-AQ-031")],
    ).model_dump(mode="json"))
    for request in policy.pending_access_requests():
        decision = policy.review_access_request(request)
        print(
            f"  {request.id}      {'APPROVED' if decision.approved else 'REFUSED'}")
        print(f"          {decision.reasons[0]}")

    rule("10. Org2 is asked to speak for Org1")
    print(
        f"  {policy.review_foreign_data(ORG1, 'an Org1 temperature observation').reasons[0]}")

    rule("11. Audit trail")
    trail = ingestion.audit_trail + quality.audit_trail + policy.audit_trail
    for entry in sorted(trail, key=lambda e: e.timestamp)[:14]:
        print(
            f"  {entry.timestamp:%H:%M:%S}  {entry.agent_id:16} {entry.action:24} {entry.outcome}")


if __name__ == "__main__":
    main()

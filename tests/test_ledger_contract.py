"""Rules the chaincode must enforce, exercised against the stand-in ledger.

These hold even when every agent is bypassed and a transaction is submitted
directly. Agent checks are advisory; these are the ones that are not.
"""

from __future__ import annotations

from datetime import timedelta

from agent_prototype.ledger import Fn
from agent_prototype.shared.models import (
    AccessDecisionRequest,
    AccessRequestSubmission,
    AssetRef,
    DivergenceFlagRequest,
    DocType,
    ForecastProduct,
    ForecastProductRequest,
    Instrument,
    ObservationAnchor,
    Provenance,
    Sensor,
    Station,
)

from tests.conftest import ORG1, ORG1_STATION, ORG2, ORG3, T0


def test_every_stored_asset_reads_back_as_its_model(
    ingestion, station_request, instrument_request, org2_ingestion, sensor_request,
    observation, calibration,
):
    """What the ledger stores must be exactly what the integration layer will
    parse. A leftover request field makes a record unreadable as its model."""
    cases = [
        (ingestion.register_station(station_request), Station),
        (ingestion.register_instrument(instrument_request), Instrument),
        (ingestion.submit_anchor(ingestion.prepare_anchor("OBS-1", observation, calibration)),
         ObservationAnchor),
        (ingestion.register_forecast_product(ForecastProductRequest(
            product_id="FC-1", product_type="nowcast", issued_at=T0, valid_from=T0,
            valid_to=T0 + timedelta(hours=6), horizon_hours=6, data_hash="c" * 64,
            provenance=Provenance(agent_id="org1.ingestion", source_system="org1-nwp",
                                  schema_version="climate-forecast/1.0", ingested_at=T0),
        )), ForecastProduct),
        (org2_ingestion.register_sensor(sensor_request), Sensor),
    ]
    for receipt, model in cases:
        assert receipt.committed, receipt.message
        model.model_validate(receipt.payload)


def test_an_organization_cannot_grant_access_to_data_it_does_not_own(ledger):
    """Org3 answering a request addressed to it still cannot release Org1's
    station. Only an asset's owner can grant it."""
    ledger.connect_as(ORG2).submit(Fn.CREATE_ACCESS_REQUEST, request=AccessRequestSubmission(
        request_id="AR-MISDIRECTED", target_org=ORG3, purpose="research",
        justification="Asking the wrong organization for Org1's station.",
        requested=[ORG1_STATION],
    ).model_dump(mode="json"))

    receipt = ledger.connect_as(ORG3).submit(
        Fn.RESPOND_TO_ACCESS_REQUEST,
        request=AccessDecisionRequest(
            decision_id="AD-FORGED", request_id="AR-MISDIRECTED", requester_org=ORG2,
            approved=True, reason="granted", granted=[ORG1_STATION],
        ).model_dump(mode="json"),
    )

    assert not receipt.committed
    assert "cannot grant access to data owned by Org1MSP" in receipt.message


# --- a divergence flag needs a grant, whoever submits it --------------------


def _anchor_both(org2_pipeline, ingestion, org2_observation, observation, calibration):
    assert org2_pipeline.ingest("OBS-ORG2", org2_observation, calibration).committed
    assert ingestion.submit_anchor(
        ingestion.prepare_anchor("OBS-ORG1", observation, calibration)
    ).committed


def _flag() -> DivergenceFlagRequest:
    return DivergenceFlagRequest(
        flag_id="DF-OBS-ORG2", org2_observation_id="OBS-ORG2",
        reference_observation_id="OBS-ORG1", reference_org=ORG1,
        divergence_metric=8.5, threshold=2.0, evidence_hash="e" * 64, detected_at=T0,
    )


def test_a_flag_without_a_grant_is_rejected_even_when_agents_are_bypassed(
    org2_pipeline, org2_quality, org2_registered, registered, ingestion,
    org2_observation, observation, calibration,
):
    _anchor_both(org2_pipeline, ingestion, org2_observation, observation, calibration)

    receipt = org2_quality.submit_divergence_flag(_flag())

    assert not receipt.committed
    assert "holds no current grant from Org1MSP" in receipt.message


def test_a_station_grant_lets_a_flag_cite_that_stations_reading(
    org2_pipeline, org2_quality, org2_registered, org2_granted, ingestion,
    org2_observation, observation, calibration,
):
    _anchor_both(org2_pipeline, ingestion, org2_observation, observation, calibration)

    assert org2_quality.submit_divergence_flag(_flag()).committed


def test_an_expired_grant_no_longer_lets_a_flag_cite_the_reading(
    org2_pipeline, org2_quality, org2_negotiation, org2_registered, registered, policy,
    ingestion, org2_observation, observation, calibration,
):
    org2_negotiation.submit_request(org2_negotiation.draft_request(
        "AR-ORG2-STALE", target_org=ORG1, purpose="validation",
        justification="Cross-checking Org2 microclimate nodes against the coastal AWS.",
        requested=[ORG1_STATION], valid_until=T0 - timedelta(days=1),
    ))
    request = policy.pending_access_requests()[0]
    policy.respond_to_access_request(request, policy.review_access_request(request))
    _anchor_both(org2_pipeline, ingestion, org2_observation, observation, calibration)

    receipt = org2_quality.submit_divergence_flag(_flag())

    assert not receipt.committed
    assert "holds no current grant" in receipt.message


def test_a_grant_for_one_station_does_not_cover_another(
    org2_pipeline, org2_quality, org2_negotiation, org2_registered, registered, policy,
    ingestion, station_request, org2_observation, observation, calibration,
):
    ingestion.register_station(station_request.model_copy(update={"station_id": "ST-OTHER"}))
    org2_negotiation.submit_request(org2_negotiation.draft_request(
        "AR-ORG2-WRONG-SITE", target_org=ORG1, purpose="validation",
        justification="Cross-checking Org2 microclimate nodes against the inland station.",
        requested=[AssetRef(owner_org=ORG1, doc_type=DocType.STATION, asset_id="ST-OTHER")],
    ))
    request = policy.pending_access_requests()[0]
    policy.respond_to_access_request(request, policy.review_access_request(request))
    _anchor_both(org2_pipeline, ingestion, org2_observation, observation, calibration)

    assert not org2_quality.submit_divergence_flag(_flag()).committed

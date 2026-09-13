"""Org1 forecast products, and the embargo that keeps pre-publication ones back."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_prototype.shared.models import (
    AccessRequest,
    AssetRef,
    DocType,
    ForecastProductRequest,
    Provenance,
)

from tests.conftest import ORG1, ORG3, T0

GULF = timezone(timedelta(hours=4))


def _forecast(product_id: str = "FC-NOWCAST-0115", embargoed_until=None):
    return ForecastProductRequest(
        product_id=product_id,
        product_type="nowcast",
        issued_at=T0,
        valid_from=T0,
        valid_to=T0 + timedelta(hours=6),
        horizon_hours=6,
        data_hash="c" * 64,
        provenance=Provenance(
            agent_id="org1.ingestion", source_system="org1-nwp",
            schema_version="climate-forecast/1.0", ingested_at=T0,
        ),
        embargoed_until=embargoed_until,
    )


def _request_for(product_id: str) -> AccessRequest:
    return AccessRequest(
        id="AR-FC", owner_org=ORG3, created_at=T0, created_by_tx="tx-1",
        target_org=ORG1, purpose="research",
        justification="Nowcast verification against the coastal station network.",
        requested=[AssetRef(owner_org=ORG1, doc_type=DocType.FORECAST_PRODUCT, asset_id=product_id)],
    )


def test_a_forecast_product_is_registered(ingestion):
    receipt = ingestion.register_forecast_product(_forecast())

    assert receipt.committed
    assert receipt.payload["owner_org"] == ORG1


def test_a_forecast_id_cannot_be_registered_twice(ingestion):
    """Re-registering is a rejection, not an overwrite of the published product."""
    assert ingestion.register_forecast_product(_forecast()).committed

    receipt = ingestion.register_forecast_product(_forecast())
    assert not receipt.committed
    assert "already exists" in receipt.message


def test_a_forecast_without_an_embargo_is_released(ingestion, policy):
    ingestion.register_forecast_product(_forecast())

    assert policy.review_access_request(_request_for("FC-NOWCAST-0115")).approved


def test_an_embargoed_forecast_is_withheld(ingestion, policy):
    ingestion.register_forecast_product(_forecast(embargoed_until=T0 + timedelta(days=30)))

    decision = policy.review_access_request(_request_for("FC-NOWCAST-0115"))
    assert not decision.approved
    assert "under embargo" in decision.reasons[0]


def test_an_embargo_set_in_gulf_time_lifts_at_the_right_moment(ingestion, policy):
    """03:00 in Sharjah is 23:00 UTC the previous day — an hour before now.
    Compared as text, "03:00" sorts after "00:00" and the embargo never lifts."""
    ingestion.register_forecast_product(
        _forecast(embargoed_until=datetime(2026, 1, 15, 3, 0, tzinfo=GULF))
    )

    assert policy.review_access_request(_request_for("FC-NOWCAST-0115")).approved


def test_an_embargo_set_in_gulf_time_still_holds_until_it_expires(ingestion, policy):
    """05:00 in Sharjah is 01:00 UTC — still an hour away."""
    ingestion.register_forecast_product(
        _forecast(embargoed_until=datetime(2026, 1, 15, 5, 0, tzinfo=GULF))
    )

    assert not policy.review_access_request(_request_for("FC-NOWCAST-0115")).approved

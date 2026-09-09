"""An in-memory stand-in for the ledger, for developing and testing agents.

**This is not a blockchain and it is not a security boundary.** It has no
identities, no signatures, no endorsement and no consensus. It enforces
ownership and referential integrity only so that agent code meets realistic
refusals during development — an agent that handles `InMemoryLedger`'s
rejections correctly will handle the real chaincode's rejections correctly.

Any authorization claim proved against this class proves nothing about the
system. The real enforcement lives in the Fabric network and its chaincode.
"""

from __future__ import annotations

import itertools
from typing import Any, Callable

from agent_prototype.ledger.port import Fn, TxReceipt, TxStatus
from agent_prototype.shared.models import DocType
from agent_prototype.shared.utilities.canonical import sha256_hex
from agent_prototype.shared.utilities.clock import Clock, SystemClock

__all__ = ["InMemoryLedger"]

_Key = tuple[str, str, str]  # (owner_org, doc_type, asset_id)


class Rejected(Exception):
    """The chaincode would refuse this transaction."""


class InMemoryLedger:
    """Implements `LedgerClient`. One instance per organization.

    Instances created with `connect_as` share one state dict, which is how two
    organizations' agents are made to see the same ledger in a test.
    """

    def __init__(
        self,
        org_id: str,
        state: dict[_Key, dict[str, Any]] | None = None,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._org_id = org_id
        self._state: dict[_Key, dict[str, Any]] = {} if state is None else state
        self._clock = clock or SystemClock()
        self._counter = itertools.count(1)
        self.submitted: list[tuple[str, dict[str, Any]]] = []

    @property
    def org_id(self) -> str:
        return self._org_id

    def connect_as(self, org_id: str) -> InMemoryLedger:
        """A second organization's view of the same ledger."""
        return InMemoryLedger(org_id, self._state, clock=self._clock)

    def close(self) -> None:  # nothing to release
        return None

    # ------------------------------------------------------------------

    def submit(self, function: Fn | str, /, **arguments: Any) -> TxReceipt:
        tx_id = sha256_hex(f"{self._org_id}:{next(self._counter)}".encode())
        self.submitted.append((str(function), arguments))
        handler = _WRITES.get(str(function))
        if handler is None:
            return TxReceipt(tx_id, TxStatus.REJECTED, message=f"unknown function {function!r}")
        try:
            payload = handler(self, arguments)
        except Rejected as exc:
            return TxReceipt(tx_id, TxStatus.REJECTED, message=str(exc))
        return TxReceipt(
            tx_id, TxStatus.COMMITTED, payload=payload, endorsing_orgs=(self._org_id,)
        )

    def evaluate(self, function: Fn | str, /, **arguments: Any) -> Any:
        handler = _READS.get(str(function))
        if handler is None:
            raise KeyError(f"unknown query {function!r}")
        return handler(self, arguments)

    # ------------------------------------------------------------------
    # State helpers. Ownership is taken from the connection, never from args.
    # ------------------------------------------------------------------

    def _put(
        self,
        doc_type: DocType,
        asset_id: str,
        value: dict[str, Any],
        *,
        drop: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        """Store a new asset. Ownership, identity and creation metadata are set
        here rather than taken from `value`, mirroring how the chaincode fills
        them in from the transaction context."""
        key = (self._org_id, str(doc_type), asset_id)
        if key in self._state:
            raise Rejected(f"{doc_type} {asset_id!r} already exists")
        record = {k: v for k, v in value.items() if k not in drop}
        record |= {
            "id": asset_id,
            "owner_org": self._org_id,
            "doc_type": str(doc_type),
            "created_at": self._clock.now().isoformat(),
            "created_by_tx": f"tx-{self._org_id}-{asset_id}",
        }
        self._state[key] = record
        return record

    def _own(self, doc_type: DocType, asset_id: str, label: str) -> dict[str, Any]:
        found = self._state.get((self._org_id, str(doc_type), asset_id))
        if found is None:
            raise Rejected(f"{label} {asset_id!r} owned by {self._org_id} does not exist")
        return found

    def _any(self, owner_org: str, doc_type: DocType, asset_id: str) -> dict[str, Any] | None:
        return self._state.get((owner_org, str(doc_type), asset_id))


# ----------------------------------------------------------------------
# Chaincode behaviour, kept deliberately thin
# ----------------------------------------------------------------------


def _register_station(led: InMemoryLedger, args: dict[str, Any]) -> dict[str, Any]:
    request = args["request"]
    return led._put(DocType.STATION, request["station_id"], {**request, "status": "ACTIVE"})


def _register_instrument(led: InMemoryLedger, args: dict[str, Any]) -> dict[str, Any]:
    request = args["request"]
    led._own(DocType.STATION, request["station_id"], "station")
    return led._put(DocType.INSTRUMENT, request["instrument_id"], {**request, "status": "ACTIVE"})


def _create_observation_anchor(led: InMemoryLedger, args: dict[str, Any]) -> dict[str, Any]:
    """Shared by every org: `station_id`/`instrument_id` name the site an
    anchor is attributed to, but what counts as valid attribution differs by
    org shape. Org1 splits site into Station+Instrument; Org2 has one
    combined Sensor asset carrying both ids. A registered Sensor satisfies
    the check on its own; otherwise fall back to Org1's pair check."""
    request = args["request"]
    sensor = led._any(led.org_id, DocType.SENSOR, request["station_id"])
    if sensor is None:
        led._own(DocType.STATION, request["station_id"], "station")
        instrument = led._own(DocType.INSTRUMENT, request["instrument_id"], "instrument")
        if instrument["station_id"] != request["station_id"]:
            raise Rejected(
                f"instrument {request['instrument_id']!r} is installed at station "
                f"{instrument['station_id']!r}, not {request['station_id']!r}"
            )
    return led._put(
        DocType.OBSERVATION_ANCHOR,
        request["observation_id"],
        {**request, "quality_status": "UNVALIDATED"},
    )


def _register_forecast_product(led: InMemoryLedger, args: dict[str, Any]) -> dict[str, Any]:
    request = args["request"]
    return led._put(DocType.FORECAST_PRODUCT, request["product_id"], request)


def _register_sensor(led: InMemoryLedger, args: dict[str, Any]) -> dict[str, Any]:
    request = args["request"]
    return led._put(DocType.SENSOR, request["sensor_id"], {**request, "status": "ACTIVE"})


def _create_quality_record(led: InMemoryLedger, args: dict[str, Any]) -> dict[str, Any]:
    request = args["request"]
    led._own(DocType.OBSERVATION_ANCHOR, request["observation_id"], "observation")
    return led._put(DocType.QUALITY_RECORD, request["observation_id"], request)


def _create_divergence_flag(led: InMemoryLedger, args: dict[str, Any]) -> dict[str, Any]:
    request = args["request"]
    led._own(DocType.OBSERVATION_ANCHOR, request["org2_observation_id"], "observation")
    reference = led._any(
        request["reference_org"], DocType.OBSERVATION_ANCHOR, request["reference_observation_id"]
    )
    if reference is None:
        raise Rejected(
            f"referenced observation {request['reference_observation_id']!r} owned by "
            f"{request['reference_org']} does not exist"
        )
    return led._put(
        DocType.DIVERGENCE_FLAG, request["flag_id"], request, drop=("flag_id",)
    )


def _create_access_request(led: InMemoryLedger, args: dict[str, Any]) -> dict[str, Any]:
    request = args["request"]
    if request["target_org"] == led.org_id:
        raise Rejected(f"{led.org_id} cannot address an access request to itself")
    return led._put(
        DocType.ACCESS_REQUEST, request["request_id"], request, drop=("request_id",)
    )


def _respond_to_access_request(led: InMemoryLedger, args: dict[str, Any]) -> dict[str, Any]:
    request = args["request"]
    pending = led._any(
        request["requester_org"], DocType.ACCESS_REQUEST, request["request_id"]
    )
    if pending is None:
        raise Rejected(f"access request {request['request_id']!r} does not exist")
    if pending["target_org"] != led.org_id:
        raise Rejected(
            f"access request {request['request_id']!r} is addressed to "
            f"{pending['target_org']}, not {led.org_id}"
        )
    return led._put(DocType.ACCESS_DECISION, request["decision_id"], request)


def _get_asset(led: InMemoryLedger, args: dict[str, Any]) -> dict[str, Any] | None:
    return led._any(args["owner_org"], DocType(args["doc_type"]), args["asset_id"])


def _list_assets(led: InMemoryLedger, args: dict[str, Any]) -> list[dict[str, Any]]:
    owner, doc_type = args["owner_org"], str(DocType(args["doc_type"]))
    return [v for (o, d, _), v in sorted(led._state.items()) if o == owner and d == doc_type]


def _verify_observation_anchor(led: InMemoryLedger, args: dict[str, Any]) -> dict[str, Any]:
    anchor = led._any(args["owner_org"], DocType.OBSERVATION_ANCHOR, args["observation_id"])
    if anchor is None:
        return {"exists": False, "matches": False, "reason": "no such anchor"}
    matches = anchor["data_hash"] == args["data_hash"]
    return {
        "exists": True,
        "matches": matches,
        "owner_org": anchor["owner_org"],
        "reason": "hash matches" if matches else "hash does not match anchor",
    }


def _list_access_requests_for(led: InMemoryLedger, args: dict[str, Any]) -> list[dict[str, Any]]:
    target = args["target_org"]
    answered = {
        v["request_id"]
        for (_, d, _), v in led._state.items()
        if d == str(DocType.ACCESS_DECISION)
    }
    return [
        v
        for (_, d, _), v in sorted(led._state.items())
        if d == str(DocType.ACCESS_REQUEST)
        and v["target_org"] == target
        and v["id"] not in answered
    ]


_WRITES: dict[str, Callable[[InMemoryLedger, dict[str, Any]], Any]] = {
    Fn.REGISTER_STATION: _register_station,
    Fn.REGISTER_INSTRUMENT: _register_instrument,
    Fn.CREATE_OBSERVATION_ANCHOR: _create_observation_anchor,
    Fn.REGISTER_FORECAST_PRODUCT: _register_forecast_product,
    Fn.CREATE_ACCESS_REQUEST: _create_access_request,
    Fn.RESPOND_TO_ACCESS_REQUEST: _respond_to_access_request,
    Fn.REGISTER_SENSOR: _register_sensor,
    Fn.CREATE_QUALITY_RECORD: _create_quality_record,
    Fn.CREATE_DIVERGENCE_FLAG: _create_divergence_flag,
}

_READS: dict[str, Callable[[InMemoryLedger, dict[str, Any]], Any]] = {
    Fn.GET_ASSET: _get_asset,
    Fn.LIST_ASSETS: _list_assets,
    Fn.VERIFY_OBSERVATION_ANCHOR: _verify_observation_anchor,
    Fn.LIST_ACCESS_REQUESTS_FOR: _list_access_requests_for,
}

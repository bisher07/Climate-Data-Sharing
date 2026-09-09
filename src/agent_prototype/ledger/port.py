"""The ledger interface — the single seam between agents and the blockchain.

Agents never speak to Hyperledger Fabric directly. They speak to a
`LedgerClient`, and the integration step supplies an implementation backed by
the real Fabric gateway. Everything in this module is a promise about what the
chaincode must offer; nothing here implements it.

Two rules keep the seam honest:

* Agents propose; the chaincode decides. A rejection is a normal outcome that
  comes back as a `TxReceipt`, not an exception, so an agent cannot mistake
  "the ledger refused" for "something broke".
* The agent's organization is a property of the connection, not an argument.
  An agent cannot claim to be acting for another organization, because there is
  nowhere to put that claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

__all__ = ["Fn", "LedgerClient", "LedgerError", "TxReceipt", "TxStatus"]


class LedgerError(Exception):
    """The ledger could not be reached or answered at all.

    Distinct from a rejection: this means the network is unreachable, the
    identity is unusable, or the chaincode is missing. A refused transaction is
    a `TxReceipt`, not this.
    """


class TxStatus(StrEnum):
    COMMITTED = "COMMITTED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class TxReceipt:
    """The outcome of a submitted transaction.

    The optional fields exist so that Section 15's metrics — endorsement
    latency, transaction latency, counts of rejected records — can be collected
    from ordinary agent runs. An implementation that cannot supply one leaves it
    at its default rather than inventing a value.
    """

    tx_id: str
    status: TxStatus
    payload: Any = None
    message: str | None = None
    endorsing_orgs: tuple[str, ...] = ()
    endorsement_seconds: float = 0.0
    commit_seconds: float = 0.0

    @property
    def committed(self) -> bool:
        return self.status is TxStatus.COMMITTED

    @property
    def total_seconds(self) -> float:
        return self.endorsement_seconds + self.commit_seconds


class Fn(StrEnum):
    """Chaincode functions the agents call.

    This enumeration is the integration contract. `docs/ledger-interface.md`
    gives the arguments and return shape of each one.
    """

    # --- writes, Org1 -----------------------------------------------------
    REGISTER_STATION = "RegisterStation"
    REGISTER_INSTRUMENT = "RegisterInstrument"
    CREATE_OBSERVATION_ANCHOR = "CreateObservationAnchor"
    REGISTER_FORECAST_PRODUCT = "RegisterForecastProduct"

    # --- writes, Org2 -----------------------------------------------------
    REGISTER_SENSOR = "RegisterSensor"
    CREATE_QUALITY_RECORD = "CreateQualityRecord"
    CREATE_DIVERGENCE_FLAG = "CreateDivergenceFlag"

    # --- writes, access negotiation --------------------------------------
    # Written by the requesting org; answered by the target org in its own
    # namespace, since neither may write into the other's.
    CREATE_ACCESS_REQUEST = "CreateAccessRequest"
    RESPOND_TO_ACCESS_REQUEST = "RespondToAccessRequest"

    # --- reads ------------------------------------------------------------
    GET_ASSET = "GetAsset"
    LIST_ASSETS = "ListAssets"
    VERIFY_OBSERVATION_ANCHOR = "VerifyObservationAnchor"
    LIST_ACCESS_REQUESTS_FOR = "ListAccessRequestsFor"


@runtime_checkable
class LedgerClient(Protocol):
    """What an agent is given. Deliberately four members and no more."""

    @property
    def org_id(self) -> str:
        """The organization this connection acts for, e.g. `"Org1MSP"`.

        Derived from the enrolled identity, never from anything an agent says.
        """

    def submit(self, function: Fn | str, /, **arguments: Any) -> TxReceipt:
        """Propose a transaction and wait for the ledger's verdict."""

    def evaluate(self, function: Fn | str, /, **arguments: Any) -> Any:
        """Run a read-only query. Returns `None` for an asset that is absent."""

    def close(self) -> None:
        """Release the underlying connection."""

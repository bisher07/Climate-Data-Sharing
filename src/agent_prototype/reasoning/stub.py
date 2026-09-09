"""Deterministic advisors, for developing and testing agents.

The parallel to `ledger/memory.py`: a stand-in that lets agent behaviour be
developed and tested without the real thing. Section 14 asks for replayable
experiment runs, and a live model is not replayable — so the same agent code
runs against a scripted advisor in tests and a real one in deployment.

`FailingAdvisor` exists because "the model is down" is a case that has to be
exercised, not assumed. An agent that degrades correctly against it degrades
correctly in production.
"""

from __future__ import annotations

from agent_prototype.reasoning.port import Advice, AdvisorError, AdvisoryRequest

__all__ = ["FailingAdvisor", "ScriptedAdvisor"]


class ScriptedAdvisor:
    """Returns pre-set advice, keyed by task. Records what it was asked."""

    def __init__(self, advice: dict[str, Advice] | None = None, *, model: str = "scripted") -> None:
        self._advice = advice or {}
        self._model = model
        self.asked: list[AdvisoryRequest] = []

    @property
    def model(self) -> str:
        return self._model

    def advise(self, request: AdvisoryRequest) -> Advice:
        self.asked.append(request)
        advice = self._advice.get(request.task, Advice())
        return Advice(
            concerns=advice.concerns,
            reasons=advice.reasons,
            explanation=advice.explanation,
            model=self._model,
        )


class FailingAdvisor:
    """Always unreachable. For proving that agents degrade rather than break."""

    @property
    def model(self) -> str:
        return "failing"

    def advise(self, request: AdvisoryRequest) -> Advice:
        raise AdvisorError("no model is reachable")

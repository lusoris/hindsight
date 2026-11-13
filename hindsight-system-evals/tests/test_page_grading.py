"""Grading a built page and the record it publishes -- no model, no server."""

from __future__ import annotations

import pytest

from hindsight_system_evals import pages
from hindsight_system_evals.corpus import HardQuestion
from hindsight_system_evals.judge import Verdict
from hindsight_system_evals.pages import PageOutcome, WaveSnapshot, grade_page, page_record

_CONTEXT = "This is a knowledge page built to answer: which release is live?"


class _StubJudge:
    """Stands in for ``evaluate``: records each call and replays canned verdicts in order."""

    def __init__(self, verdicts: list[Verdict]) -> None:
        self._verdicts = list(verdicts)
        self.calls: list[tuple[str, str, str | None, bool]] = []

    async def __call__(
        self, response: str, criteria: str, context: str | None = None, *, confirm_met: bool = False
    ) -> Verdict:
        self.calls.append((response, criteria, context, confirm_met))
        return self._verdicts.pop(0)


def _question(must_not_claim: str = "") -> HardQuestion:
    return HardQuestion(
        id="q1",
        category="temporal",
        question="Which release is live?",
        ideal_query="live release",
        answer_criteria="The page says 0.9.3 is live.",
        must_not_claim=must_not_claim,
    )


def _outcome() -> PageOutcome:
    return PageOutcome(
        question_id="q1",
        category="temporal",
        bank_id="bank-1",
        page_id="page-1",
        waves=[
            WaveSnapshot(wave=1, facts_ingested=2, content="abc"),
            WaveSnapshot(wave=2, facts_ingested=3, content="abcdef"),
        ],
        final_content="abcdef",
    )


async def test_grade_page_judges_answer_by_default_and_trap_with_confirm_met(monkeypatch: pytest.MonkeyPatch) -> None:
    judge = _StubJudge([Verdict(True, "answers"), Verdict(False, "no trap")])
    monkeypatch.setattr(pages, "evaluate", judge)
    outcome = _outcome()

    await grade_page(outcome, _question("0.9.2 is live"), _CONTEXT)

    assert judge.calls == [
        ("abcdef", "The page says 0.9.3 is live.", _CONTEXT, False),
        ("abcdef", "The page asserts, as currently true, that 0.9.2 is live.", _CONTEXT, True),
    ]
    assert (outcome.correct, outcome.correct_reason) == (True, "answers")
    assert (outcome.hit_trap, outcome.trap_reason) == (False, "no trap")


async def test_grade_page_records_a_trap_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pages, "evaluate", _StubJudge([Verdict(False, "incomplete"), Verdict(True, "asserts it")]))
    outcome = _outcome()

    await grade_page(outcome, _question("0.9.2 is live"), _CONTEXT)

    assert (outcome.correct, outcome.hit_trap, outcome.trap_reason) == (False, True, "asserts it")


async def test_grade_page_skips_the_trap_without_must_not_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    judge = _StubJudge([Verdict(True, "answers")])
    monkeypatch.setattr(pages, "evaluate", judge)
    outcome = _outcome()

    await grade_page(outcome, _question(), _CONTEXT)
    assert len(judge.calls) == 1
    assert judge.calls[0][3] is False
    assert outcome.hit_trap is False
    assert outcome.trap_reason == ""


def test_page_record_explains_a_trap_hit_with_the_trap_reason() -> None:
    outcome = _outcome()
    outcome.correct, outcome.correct_reason = True, "answers"
    outcome.hit_trap, outcome.trap_reason = True, "asserts it"

    record = page_record("delta_update", "case-1", _question(), outcome)

    assert record.kind == "delta_update"
    assert record.question_id == "case-1"
    assert record.category == "temporal"
    assert (record.correct, record.hit_trap) == (True, True)
    assert record.reason == "asserts it"
    assert record.sizes == [3, 6]
    assert (record.bank_id, record.page_id) == ("bank-1", "page-1")


def test_page_record_uses_the_correct_reason_without_a_trap_hit() -> None:
    outcome = _outcome()
    outcome.correct, outcome.correct_reason = False, "incomplete"
    outcome.trap_reason = "stale trap text"

    record = page_record("knowledge_page", "q1", _question(), outcome)

    assert record.kind == "knowledge_page"
    assert record.hit_trap is False
    assert record.reason == "incomplete"

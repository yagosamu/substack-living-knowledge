"""ADR 0003 rule 4, enforced: step outputs over the journal budget fail permanently, in the
fake and in the production adapter alike (docs/contract-checklist.md, section 4)."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import cast

import inngest
import pytest

from living_kb.config import Settings
from living_kb.durable import InngestRunner, JournalRunner, PermanentError, StepRunner


class FakeInngestStep:
    async def run(self, step_id: str, handler: Callable[[], Awaitable[object]]) -> object:
        return await handler()


class FakeInngestContext:
    def __init__(self) -> None:
        self.step = FakeInngestStep()


def test_the_fake_rejects_a_step_output_over_the_budget_and_does_not_journal_it() -> None:
    calls = 0

    def oversized() -> str:
        nonlocal calls
        calls += 1
        return "x" * 200

    async def pipeline(runner: StepRunner) -> object:
        return await runner.run("fetch-article", oversized)

    runner = JournalRunner(max_output_bytes=100)
    for _ in range(2):
        with pytest.raises(PermanentError, match="fetch-article"):
            asyncio.run(runner.execute(pipeline))

    assert calls == 2


def test_the_fake_accepts_an_output_exactly_at_the_budget() -> None:
    # json.dumps("x" * 98) is 100 bytes including its quotes.
    runner = JournalRunner(max_output_bytes=100)

    assert asyncio.run(runner.run("chunk", lambda: "x" * 98)) == "x" * 98


def test_the_production_adapter_rejects_an_oversized_output_as_non_retriable() -> None:
    runner = InngestRunner(cast(inngest.Context, FakeInngestContext()), max_output_bytes=100)

    with pytest.raises(inngest.NonRetriableError, match="embed"):
        asyncio.run(runner.run("embed", lambda: [0.123456789] * 50))


def test_the_default_budget_comes_from_settings_and_fits_a_long_article() -> None:
    # A 60-passage article of 1536-dim vectors must fit the default budget.
    budget = Settings(_env_file=None).max_step_output_bytes
    vectors = [[0.0123456789] * 1536 for _ in range(60)]
    runner = JournalRunner()

    assert asyncio.run(runner.run("embed", lambda: vectors)) == vectors
    assert budget >= 1_000_000

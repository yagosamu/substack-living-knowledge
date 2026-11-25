"""The production entry point: a PermanentError raised anywhere in a durable function, not
only inside a step, must reach Inngest as non-retriable (PR #28 review, finding 1)."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, cast

import inngest
import pytest

from living_kb.durable import PermanentError, StepRunner, run_durable


class FakeInngestStep:
    def __init__(self) -> None:
        self.step_ids: list[str] = []

    async def run(self, step_id: str, handler: Callable[[], Awaitable[object]]) -> object:
        self.step_ids.append(step_id)
        return await handler()


class FakeEvent:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data


class FakeInngestContext:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self.step = FakeInngestStep()
        self.event = FakeEvent(data or {})


def ctx(data: dict[str, Any] | None = None) -> inngest.Context:
    return cast(inngest.Context, FakeInngestContext(data))


def test_a_permanent_error_raised_outside_any_step_reaches_inngest_as_non_retriable() -> None:
    async def pipeline(runner: StepRunner) -> object:
        raise PermanentError("malformed kb/article.discovered event")

    with pytest.raises(inngest.NonRetriableError, match="malformed") as raised:
        asyncio.run(run_durable(ctx(), pipeline))

    assert isinstance(raised.value.__cause__, PermanentError)


def test_other_errors_raised_outside_steps_stay_retriable() -> None:
    async def pipeline(runner: StepRunner) -> object:
        raise ConnectionError("database restarting")

    with pytest.raises(ConnectionError):
        asyncio.run(run_durable(ctx(), pipeline))


def test_run_durable_runs_the_pipeline_through_the_engine_and_returns_its_result() -> None:
    context = ctx()

    async def pipeline(runner: StepRunner) -> object:
        return await runner.run("greet", lambda: "hello")

    assert asyncio.run(run_durable(context, pipeline)) == "hello"
    assert cast(FakeInngestContext, context).step.step_ids == ["greet"]


def test_the_registered_ingest_handler_rejects_a_malformed_event_as_non_retriable() -> None:
    # The production path end to end: no OpenAI client or network is touched, because a
    # malformed event must fail before any port is used.
    from living_kb.inngest_app import handle_ingest_article

    with pytest.raises(inngest.NonRetriableError):
        asyncio.run(handle_ingest_article(ctx({"url": None})))

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import cast

import inngest
import pytest

from living_kb.durable import (
    InjectedFailure,
    InngestRunner,
    JournalRunner,
    PermanentError,
    StepRunner,
)


def test_run_executes_a_sync_step_and_returns_its_result() -> None:
    runner = JournalRunner()

    assert asyncio.run(runner.run("add", lambda: 1 + 1)) == 2


def test_run_awaits_an_async_step_and_returns_its_result() -> None:
    async def fetch() -> str:
        return "<html>"

    runner = JournalRunner()

    assert asyncio.run(runner.run("fetch", fetch)) == "<html>"


class Counter:
    """A step body that counts how often it really executes."""

    def __init__(self, result: object = "done") -> None:
        self.calls = 0
        self.result = result

    def __call__(self) -> object:
        self.calls += 1
        return self.result


def test_replaying_a_run_returns_journaled_results_without_re_executing() -> None:
    fetch = Counter("<html>")

    async def pipeline(runner: StepRunner) -> object:
        return await runner.run("fetch", fetch)

    runner = JournalRunner()
    first = asyncio.run(runner.execute(pipeline))
    replayed = asyncio.run(runner.execute(pipeline))

    assert (first, replayed, fetch.calls) == ("<html>", "<html>", 1)


def test_failure_at_a_step_resumes_there_without_re_executing_earlier_steps() -> None:
    fetch, parse, embed = Counter("<html>"), Counter("text"), Counter([0.1, 0.2])

    async def pipeline(runner: StepRunner) -> object:
        await runner.run("fetch", fetch)
        await runner.run("parse", parse)
        return await runner.run("embed", embed)

    runner = JournalRunner()
    runner.fail_next("embed")

    with pytest.raises(InjectedFailure):
        asyncio.run(runner.execute(pipeline))
    assert (fetch.calls, parse.calls, embed.calls) == (1, 1, 0)

    assert asyncio.run(runner.execute(pipeline)) == [0.1, 0.2]
    assert (fetch.calls, parse.calls, embed.calls) == (1, 1, 1)


def test_failure_after_a_side_effect_re_executes_the_step_at_least_once_more() -> None:
    # The step's body ran (e.g. rows were committed), then the process died before the
    # journal recorded it. A durable engine is at-least-once: the step runs again.
    persist = Counter("article-id")

    async def pipeline(runner: StepRunner) -> object:
        return await runner.run("persist", persist)

    runner = JournalRunner()
    runner.fail_next("persist", after_side_effect=True)

    with pytest.raises(InjectedFailure):
        asyncio.run(runner.execute(pipeline))
    assert persist.calls == 1

    assert asyncio.run(runner.execute(pipeline)) == "article-id"
    assert persist.calls == 2


def test_a_step_output_that_is_not_json_serializable_is_rejected_and_not_journaled() -> None:
    clock = Counter(datetime(2026, 11, 21, tzinfo=UTC))

    async def pipeline(runner: StepRunner) -> object:
        return await runner.run("read-clock", clock)

    runner = JournalRunner()

    with pytest.raises(TypeError):
        asyncio.run(runner.execute(pipeline))
    with pytest.raises(TypeError):
        asyncio.run(runner.execute(pipeline))
    assert clock.calls == 2


def test_step_output_comes_back_as_its_json_round_trip_on_first_run_and_on_replay() -> None:
    # A real engine hands back the journaled JSON on replay. Returning the same shape on
    # the first run too keeps code from depending on types that would not survive replay.
    chunk = Counter(("first passage", "second passage"))

    async def pipeline(runner: StepRunner) -> object:
        return await runner.run("chunk", chunk)

    runner = JournalRunner()
    first = asyncio.run(runner.execute(pipeline))
    replayed = asyncio.run(runner.execute(pipeline))

    assert first == replayed == ["first passage", "second passage"]


def test_reusing_a_step_name_within_one_execution_is_rejected() -> None:
    # Step names are the journal's keys: a second "embed" would silently replay the first.
    async def pipeline(runner: StepRunner) -> object:
        await runner.run("embed", Counter([0.1]))
        return await runner.run("embed", Counter([0.2]))

    with pytest.raises(ValueError, match="embed"):
        asyncio.run(JournalRunner().execute(pipeline))


class FakeInngestStep:
    """Stands in for Inngest's `ctx.step`: records step ids and runs handlers, unless a
    journaled value is supplied, in which case it returns that value without running."""

    def __init__(self, journaled: dict[str, object] | None = None) -> None:
        self.journaled = journaled or {}
        self.step_ids: list[str] = []

    async def run(self, step_id: str, handler: Callable[[], Awaitable[object]]) -> object:
        self.step_ids.append(step_id)
        if step_id in self.journaled:
            return self.journaled[step_id]
        return await handler()


class FakeInngestContext:
    def __init__(self, step: FakeInngestStep) -> None:
        self.step = step


def inngest_runner(step: FakeInngestStep) -> InngestRunner:
    return InngestRunner(cast(inngest.Context, FakeInngestContext(step)))


def test_inngest_runner_runs_each_step_through_ctx_step_run_under_the_same_name() -> None:
    step = FakeInngestStep()

    result = asyncio.run(inngest_runner(step).run("fetch-html", lambda: "<html>"))

    assert (step.step_ids, result) == (["fetch-html"], "<html>")


def test_inngest_runner_returns_the_engines_journaled_value_without_running_the_step() -> None:
    fetch = Counter("<fresh html>")
    step = FakeInngestStep(journaled={"fetch": "<journaled html>"})

    result = asyncio.run(inngest_runner(step).run("fetch", fetch))

    assert (result, fetch.calls) == ("<journaled html>", 0)


def test_inngest_runner_awaits_async_steps_inside_the_engine_handler() -> None:
    async def embed() -> list[float]:
        return [0.1, 0.2]

    result = asyncio.run(inngest_runner(FakeInngestStep()).run("embed", embed))

    assert result == [0.1, 0.2]


def test_a_permanent_error_inside_a_step_reaches_inngest_as_non_retriable() -> None:
    def check_allowlist() -> None:
        raise PermanentError("https://evil.example.io is outside the publications registry")

    with pytest.raises(inngest.NonRetriableError) as raised:
        asyncio.run(inngest_runner(FakeInngestStep()).run("check-allowlist", check_allowlist))

    assert isinstance(raised.value.__cause__, PermanentError)
    assert "evil.example.io" in str(raised.value)


def test_other_errors_inside_a_step_stay_retriable() -> None:
    def fetch() -> None:
        raise ConnectionError("timed out")

    with pytest.raises(ConnectionError):
        asyncio.run(inngest_runner(FakeInngestStep()).run("fetch", fetch))

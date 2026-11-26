"""Durable step-runner seam, its journaling fake, and the Inngest adapter."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol, cast, overload

from living_kb.config import settings
from living_kb.logging import bind_run, bind_step, reset

if TYPE_CHECKING:
    import inngest


class StepRunner(Protocol):
    """The only operation a durable pipeline needs from its engine."""

    @overload
    async def run[T](self, name: str, fn: Callable[[], Awaitable[T]]) -> T: ...
    @overload
    async def run[T](self, name: str, fn: Callable[[], T]) -> T: ...


class InjectedFailure(RuntimeError):
    """A simulated crash raised by JournalRunner at a named step."""


class PermanentError(Exception):
    """A failure that retrying the step cannot fix."""


def _journal_payload(name: str, value: object, budget: int) -> str:
    """Serialize one step output and enforce ADR 0003's journal budget."""
    payload = json.dumps(value)
    if len(payload.encode("utf-8")) > budget:
        raise PermanentError(f"step {name!r} output exceeds the {budget}-byte journal budget")
    return payload


@overload
async def _call_step[T](fn: Callable[[], Awaitable[T]]) -> T: ...
@overload
async def _call_step[T](fn: Callable[[], T]) -> T: ...
async def _call_step(fn: Callable[[], object]) -> object:
    """Invoke a sync or async step body and return its result."""
    result = fn()
    if isinstance(result, Awaitable):
        return await result
    return result


class JournalRunner:
    """In-memory runner that journals JSON step outputs for replay in tests."""

    def __init__(self, *, max_output_bytes: int | None = None) -> None:
        self._max_output_bytes = (
            settings.max_step_output_bytes if max_output_bytes is None else max_output_bytes
        )
        self._journal: dict[str, str] = {}
        self._pending_failures: dict[str, bool] = {}
        self._seen: set[str] = set()

    @overload
    async def run[T](self, name: str, fn: Callable[[], Awaitable[T]]) -> T: ...
    @overload
    async def run[T](self, name: str, fn: Callable[[], T]) -> T: ...
    async def run(self, name: str, fn: Callable[[], object]) -> object:
        token = bind_step(name)
        try:
            if name in self._seen:
                raise ValueError(f"duplicate step name: {name}")
            self._seen.add(name)

            after_side_effect = self._pending_failures.pop(name, None)
            if after_side_effect is True:
                await _call_step(fn)
                raise InjectedFailure(f"injected failure after side effect in step {name!r}")
            if after_side_effect is False:
                raise InjectedFailure(f"injected failure in step {name!r}")

            if name in self._journal:
                return cast(object, json.loads(self._journal[name]))

            payload = _journal_payload(name, await _call_step(fn), self._max_output_bytes)
            self._journal[name] = payload
            return cast(object, json.loads(payload))
        finally:
            reset(token)

    async def execute[T](
        self,
        pipeline: Callable[[StepRunner], Awaitable[T]],
        *,
        run_id: str | None = None,
        function_name: str | None = None,
    ) -> T:
        """Run a pipeline, allowing a fresh attempt to replay the same journal."""
        token = bind_run(run_id=run_id, function=function_name or "unknown") if run_id else None
        try:
            self._seen.clear()
            return await pipeline(self)
        finally:
            if token is not None:
                reset(token)

    def fail_next(
        self,
        name: str,
        *,
        after_side_effect: bool = False,
    ) -> None:
        """Fail the next execution of the named step once."""
        self._pending_failures[name] = after_side_effect


class InngestRunner:
    """Map the step-runner seam onto Inngest's step API."""

    def __init__(self, ctx: inngest.Context, *, max_output_bytes: int | None = None) -> None:
        self._ctx = ctx
        self._max_output_bytes = (
            settings.max_step_output_bytes if max_output_bytes is None else max_output_bytes
        )
        self._seen: set[str] = set()

    @overload
    async def run[T](self, name: str, fn: Callable[[], Awaitable[T]]) -> T: ...
    @overload
    async def run[T](self, name: str, fn: Callable[[], T]) -> T: ...
    async def run(self, name: str, fn: Callable[[], object]) -> object:
        if name in self._seen:
            raise ValueError(f"duplicate step name: {name}")
        self._seen.add(name)

        import inngest as inngest_sdk

        async def handler() -> object:
            try:
                result = await _call_step(fn)
                _journal_payload(name, result, self._max_output_bytes)
                return result
            except PermanentError as exc:
                raise inngest_sdk.NonRetriableError(str(exc)) from exc

        token = bind_step(name)
        try:
            return await self._ctx.step.run(name, handler)
        finally:
            reset(token)


async def run_durable[T](
    ctx: inngest.Context,
    pipeline: Callable[[StepRunner], Awaitable[T]],
    *,
    function_name: str | None = None,
) -> T:
    """Run a durable function, correlating its logs and translating permanent failures."""
    run_id = str(getattr(ctx, "run_id", "unknown"))
    function = function_name or str(getattr(ctx, "function_id", "unknown"))
    token = bind_run(run_id=run_id, function=function)
    try:
        return await pipeline(InngestRunner(ctx))
    except PermanentError as exc:
        import inngest as inngest_sdk

        raise inngest_sdk.NonRetriableError(str(exc)) from exc
    finally:
        reset(token)

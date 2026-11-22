"""Durable step-runner seam, its journaling fake, and the Inngest adapter."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol, cast, overload

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

    def __init__(self) -> None:
        self._journal: dict[str, str] = {}
        self._pending_failures: dict[str, bool] = {}
        self._seen: set[str] = set()

    @overload
    async def run[T](self, name: str, fn: Callable[[], Awaitable[T]]) -> T: ...
    @overload
    async def run[T](self, name: str, fn: Callable[[], T]) -> T: ...
    async def run(self, name: str, fn: Callable[[], object]) -> object:
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

        self._journal[name] = json.dumps(await _call_step(fn))
        return cast(object, json.loads(self._journal[name]))

    async def execute[T](self, pipeline: Callable[[StepRunner], Awaitable[T]]) -> T:
        """Run a pipeline, allowing a fresh attempt to replay the same journal."""
        self._seen.clear()
        return await pipeline(self)

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

    def __init__(self, ctx: inngest.Context) -> None:
        self._ctx = ctx
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
                return await _call_step(fn)
            except PermanentError as exc:
                raise inngest_sdk.NonRetriableError(str(exc)) from exc

        return await self._ctx.step.run(name, handler)

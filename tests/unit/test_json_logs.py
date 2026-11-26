"""JSON logs: the rendered line, the run context and the durable wiring."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

import inngest
import pytest

from living_kb.durable import JournalRunner, StepRunner, run_durable
from living_kb.logging import JsonFormatter

CONTEXT_KEYS = {"run_id", "function", "step"}
log = logging.getLogger("living_kb.test.logs")


@pytest.fixture(autouse=True)
def json_lines(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> pytest.LogCaptureFixture:
    for name in (
        "living_kb",
        "living_kb.test.logs",
        "living_kb.api",
        "living_kb.inngest",
        "living_kb.edge",
        "uvicorn.error",
    ):
        named = logging.getLogger(name)
        monkeypatch.setattr(named, "disabled", False)
        monkeypatch.setattr(named, "level", logging.INFO)
    caplog.set_level(logging.INFO)
    caplog.handler.setFormatter(JsonFormatter())
    caplog.clear()
    return caplog


def payloads(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [cast("dict[str, Any]", json.loads(line)) for line in caplog.text.splitlines()]


class FakeStep:
    async def run(self, step_id: str, handler: Callable[[], Awaitable[object]]) -> object:
        return await handler()


class FakeEvent:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.id = "event-id-that-is-not-the-run-id"


class FakeContext:
    def __init__(self, run_id: str, data: dict[str, Any] | None = None) -> None:
        self.run_id = run_id
        self.step = FakeStep()
        self.event = FakeEvent(data or {})
        self.function_id = "function-id-that-is-not-the-function-name"


def context(run_id: str, data: dict[str, Any] | None = None) -> inngest.Context:
    return cast("inngest.Context", FakeContext(run_id, data))


def test_timestamp_comes_from_the_record_not_from_the_wall_clock() -> None:
    record = logging.LogRecord("living_kb.edge", logging.INFO, __file__, 1, "old line", (), None)
    record.created = 1_700_000_000.125

    line = cast("dict[str, Any]", json.loads(JsonFormatter().format(record)))

    assert line["timestamp"] == "2023-11-14T22:13:20.125Z"
    assert line["message"] == "old line"


def test_a_log_line_has_timestamp_level_logger_and_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logging.getLogger("living_kb.api.request").info("GET /health %s", 200)

    (line,) = payloads(caplog)
    assert set(line) == {"timestamp", "level", "logger", "message"}
    assert (line["level"], line["logger"], line["message"]) == (
        "INFO",
        "living_kb.api.request",
        "GET /health 200",
    )
    timestamp = datetime.fromisoformat(cast("str", line["timestamp"]).replace("Z", "+00:00"))
    assert timestamp.utcoffset() == timedelta(0)
    assert timestamp.timestamp() == pytest.approx(caplog.records[0].created, abs=0.001)


def test_an_empty_message_and_a_failure_are_still_one_json_line_each(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logging.getLogger("living_kb.edge").info("")

    try:
        raise ValueError("a real failure")
    except ValueError:
        logging.getLogger("living_kb.edge").exception("failed %s", "embed")

    first, second = payloads(caplog)
    assert first["message"] == ""
    assert second["message"] == "failed embed"
    assert second["level"] == "ERROR"
    assert "ValueError: a real failure" in cast("str", second["exception"])


def test_each_journal_step_binds_its_name_inside_the_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fetch() -> str:
        log.info("inside fetch")
        return "page"

    def allowlist() -> bool:
        log.info("inside allowlist")
        return True

    async def pipeline(runner: StepRunner) -> object:
        log.info("before steps")
        await runner.run("fetch-article", fetch)
        log.info("between steps")
        await runner.run("check-allowlist", allowlist)
        log.info("after steps")
        return None

    asyncio.run(
        JournalRunner().execute(pipeline, run_id="journal-run-77", function_name="ingest-article")
    )

    lines = payloads(caplog)
    assert [line["message"] for line in lines] == [
        "before steps",
        "inside fetch",
        "between steps",
        "inside allowlist",
        "after steps",
    ]
    assert {line["run_id"] for line in lines} == {"journal-run-77"}
    assert {line["function"] for line in lines} == {"ingest-article"}
    assert [line.get("step") for line in lines] == [
        None,
        "fetch-article",
        None,
        "check-allowlist",
        None,
    ]
    for line in (lines[0], lines[2], lines[4]):
        assert "step" not in line


def test_the_production_entry_point_uses_the_run_id_and_the_given_function_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def fetch_article() -> str:
        log.info("inside fetch-article")
        return "page"

    async def pipeline(runner: StepRunner) -> str:
        log.info("started")
        await runner.run("fetch-article", fetch_article)
        return "done"

    result = asyncio.run(
        run_durable(context("prod-run-42"), pipeline, function_name="ingest-article")
    )

    assert result == "done"
    lines = payloads(caplog)
    assert {line["run_id"] for line in lines} == {"prod-run-42"}
    assert {line["function"] for line in lines} == {"ingest-article"}
    assert [line.get("step") for line in lines] == [None, "fetch-article"]

    logging.getLogger("living_kb.api").info("request after the run")
    assert CONTEXT_KEYS.isdisjoint(payloads(caplog)[-1])


def test_the_ingest_handler_correlates_its_start_and_failure_lines(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from living_kb.inngest_app import handle_ingest_article

    with pytest.raises(inngest.NonRetriableError):
        asyncio.run(handle_ingest_article(context("handler-run-5", {"url": None})))

    started, failed = [line for line in payloads(caplog) if "run_id" in line]
    assert (started["message"], failed["message"]) == (
        "ingest-article started",
        "ingest-article failed",
    )
    assert {started["run_id"], failed["run_id"]} == {"handler-run-5"}
    assert {started["function"], failed["function"]} == {"ingest-article"}
    assert "step" not in started
    assert "error" in failed
    assert "Traceback (most recent call last)" in cast("str", failed["exception"])
    assert "PermanentError" in cast("str", failed["exception"])


def test_sequential_runs_do_not_inherit_the_previous_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def pipeline(runner: StepRunner) -> str:
        log.info("hello")
        return cast("str", await runner.run("greet", lambda: "hello"))

    async def scenario() -> None:
        await run_durable(context("first-run"), pipeline, function_name="first-function")
        logging.getLogger("living_kb.api").info("between runs")
        await run_durable(context("second-run"), pipeline, function_name="second-function")

    asyncio.run(scenario())

    first, middle, second = payloads(caplog)
    assert (first["run_id"], first["function"]) == ("first-run", "first-function")
    assert CONTEXT_KEYS.isdisjoint(middle)
    assert (second["run_id"], second["function"]) == ("second-run", "second-function")


def test_concurrent_runs_keep_their_own_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def one(run_id: str, marker: str) -> str:
        def fetch() -> str:
            log.info(marker)
            return marker

        async def pipeline(runner: StepRunner) -> str:
            await asyncio.sleep(0)
            return cast("str", await runner.run("fetch-article", fetch))

        return await run_durable(context(run_id), pipeline, function_name=f"fn-{marker}")

    async def both() -> list[str]:
        return cast(
            "list[str]",
            await asyncio.gather(one("run-alpha", "alpha"), one("run-beta", "beta")),
        )

    assert asyncio.run(both()) == ["alpha", "beta"]

    by_message = {line["message"]: line for line in payloads(caplog)}
    assert (by_message["alpha"]["run_id"], by_message["alpha"]["function"]) == (
        "run-alpha",
        "fn-alpha",
    )
    assert (by_message["beta"]["run_id"], by_message["beta"]["function"]) == (
        "run-beta",
        "fn-beta",
    )
    assert by_message["alpha"]["step"] == "fetch-article"


def test_the_uvicorn_log_config_renders_json_before_the_app_is_imported() -> None:
    config_path = Path(__file__).resolve().parents[2] / "infra" / "log-config.json"
    script = (
        "import json, logging, logging.config, sys; "
        "logging.config.dictConfig(json.load(open(sys.argv[1]))); "
        "logging.getLogger('uvicorn.error').info('pre-app startup')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(config_path)],
        capture_output=True,
        text=True,
        check=True,
    )

    lines = [
        cast("dict[str, Any]", json.loads(line))
        for line in (result.stdout + result.stderr).splitlines()
        if line.lstrip().startswith("{")
    ]
    assert [line["message"] for line in lines] == ["pre-app startup"]
    assert set(lines[0]) == {"timestamp", "level", "logger", "message"}


def test_importing_the_api_installs_json_output_for_startup_logs() -> None:
    script = (
        "import logging, living_kb.api; "
        "logging.getLogger('uvicorn.error').info('startup line'); "
        "logging.getLogger('living_kb.inngest').info('ready line')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )

    lines = [
        cast("dict[str, Any]", json.loads(line))
        for line in (result.stdout + result.stderr).splitlines()
        if line.lstrip().startswith("{")
    ]
    chosen = [line for line in lines if line["message"] in ("startup line", "ready line")]
    assert [line["message"] for line in chosen] == ["startup line", "ready line"]
    assert {line["logger"] for line in chosen} == {"uvicorn.error", "living_kb.inngest"}
    for line in chosen:
        assert CONTEXT_KEYS.isdisjoint(line)


class BareContext:
    """A context with no run id and no function id, like the older contract fakes."""

    def __init__(self) -> None:
        self.step = FakeStep()
        self.event = FakeEvent({})


class Opaque:
    def __init__(self, value: str = "opaque") -> None:
        self.value = value

    def __str__(self) -> str:
        return self.value


def test_a_raised_journal_step_does_not_leak_its_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def bad() -> str:
        log.info("inside bad")
        raise ValueError("bad step")

    async def pipeline(runner: StepRunner) -> None:
        with pytest.raises(ValueError):
            await runner.run("bad-step", bad)
        log.info("after bad")

    asyncio.run(
        JournalRunner().execute(pipeline, run_id="journal-run-9", function_name="journal-fn")
    )

    lines = payloads(caplog)
    assert [line["message"] for line in lines] == ["inside bad", "after bad"]
    assert lines[0]["step"] == "bad-step"
    assert "step" not in lines[1]


def test_a_raised_production_step_does_not_leak_its_name(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def bad() -> str:
        log.info("inside bad")
        raise ValueError("bad step")

    def good() -> str:
        log.info("inside good")
        return "ok"

    async def pipeline(runner: StepRunner) -> None:
        with pytest.raises(ValueError):
            await runner.run("bad-step", bad)
        log.info("between steps")
        await runner.run("good-step", good)
        log.info("after steps")

    asyncio.run(run_durable(context("prod-run-9"), pipeline, function_name="ingest-article"))

    lines = payloads(caplog)
    assert [line.get("step") for line in lines] == ["bad-step", None, "good-step", None]
    for line in (lines[1], lines[3]):
        assert "step" not in line
    assert {line["run_id"] for line in lines} == {"prod-run-9"}


def test_execute_resets_the_run_context_even_when_the_pipeline_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def boom(runner: StepRunner) -> str:
        log.info("inside boom")
        raise RuntimeError("boom")

    async def okay(runner: StepRunner) -> str:
        log.info("inside okay")
        return "ok"

    async def scenario() -> None:
        runner = JournalRunner()
        with pytest.raises(RuntimeError):
            await runner.execute(boom, run_id="first-run", function_name="first-fn")
        logging.getLogger("living_kb.api").info("between executes")
        assert (
            await JournalRunner().execute(okay, run_id="second-run", function_name="second-fn")
            == "ok"
        )

    asyncio.run(scenario())

    first, between, second = payloads(caplog)
    assert (first["run_id"], first["function"]) == ("first-run", "first-fn")
    assert CONTEXT_KEYS.isdisjoint(between)
    assert (second["run_id"], second["function"]) == ("second-run", "second-fn")


def test_execute_binds_only_a_complete_run_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def pipeline(runner: StepRunner) -> str:
        log.info("hello")
        return "done"

    asyncio.run(JournalRunner().execute(pipeline, run_id="run-without-function"))
    asyncio.run(JournalRunner().execute(pipeline, function_name="fn-without-run"))

    lines = payloads(caplog)
    assert len(lines) == 2
    for line in lines:
        assert CONTEXT_KEYS.isdisjoint(line)


def test_missing_engine_ids_are_omitted_instead_of_becoming_unknown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def pipeline(runner: StepRunner) -> str:
        log.info("no ids here")
        return "done"

    asyncio.run(run_durable(cast("inngest.Context", BareContext()), pipeline))

    (line,) = payloads(caplog)
    assert CONTEXT_KEYS.isdisjoint(line)
    assert "unknown" not in json.dumps(line)


def test_caller_extras_are_kept_but_cannot_forge_context_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    forged = {
        "run_id": "forged-run",
        "function": "forged-fn",
        "step": "forged-step",
        "canonical_id": "kb:pub:post",
        "article_id": 7,
    }

    logging.getLogger("living_kb.edge").info("outside", extra=forged)

    async def pipeline(runner: StepRunner) -> str:
        log.info("inside", extra=forged)
        return "done"

    asyncio.run(JournalRunner().execute(pipeline, run_id="real-run", function_name="real-fn"))

    outside, inside = payloads(caplog)
    assert (outside["canonical_id"], outside["article_id"]) == ("kb:pub:post", 7)
    assert CONTEXT_KEYS.isdisjoint(outside)
    assert (inside["canonical_id"], inside["article_id"]) == ("kb:pub:post", 7)
    assert (inside["run_id"], inside["function"]) == ("real-run", "real-fn")
    assert "step" not in inside


def test_non_json_extras_do_not_drop_the_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logging.getLogger("living_kb.edge").info(
        "odd extras",
        extra={"when": datetime(2024, 1, 2, 3, 4, 5), "opaque": Opaque()},
    )

    (line,) = payloads(caplog)
    assert line["message"] == "odd extras"
    assert line["when"] == "2024-01-02 03:04:05"
    assert line["opaque"] == "opaque"


def test_exception_and_stack_info_are_both_kept(
    caplog: pytest.LogCaptureFixture,
) -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("living_kb.edge").error("with both", exc_info=True, stack_info=True)

    (line,) = payloads(caplog)
    assert "ValueError: boom" in cast("str", line["exception"])
    assert "Stack (most recent call last):" in cast("str", line["stack"])


def test_color_message_extra_is_not_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logging.getLogger("uvicorn.error").info("ansi", extra={"color_message": "\x1b[36mansi\x1b[0m"})

    (line,) = payloads(caplog)
    assert line["message"] == "ansi"
    assert set(line) == {"timestamp", "level", "logger", "message"}


def test_configured_handlers_are_replaced_not_duplicated() -> None:
    config_path = Path(__file__).resolve().parents[2] / "infra" / "log-config.json"
    script = (
        "import json, logging, logging.config, sys; "
        "logging.config.dictConfig(json.load(open(sys.argv[1]))); "
        "import living_kb.api; "
        "from living_kb.logging import configure_logging; "
        "configure_logging(); "
        "configure_logging(); "
        "print('root-handlers', len(logging.getLogger().handlers)); "
        "logging.getLogger('uvicorn.error').info('exactly once')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(config_path)],
        capture_output=True,
        text=True,
        check=True,
    )

    lines = [
        cast("dict[str, Any]", json.loads(line))
        for line in (result.stdout + result.stderr).splitlines()
        if line.lstrip().startswith("{")
    ]
    assert [line["message"] for line in lines] == ["exactly once"]
    assert "root-handlers 1" in (result.stdout + result.stderr).splitlines()


def test_demo_durable_logs_through_the_seam(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from living_kb import inngest_app

    async def pipeline(runner: StepRunner, *, name: str) -> dict[str, str]:
        log.info("demo %s", name)
        return await runner.run("greet", lambda: {"greeting": name.upper()})

    monkeypatch.setattr(inngest_app, "demo_pipeline", pipeline)

    result = asyncio.run(
        cast("Any", inngest_app.demo_durable)._handler(context("demo-run-3", {"name": "ada"}))
    )

    assert result == {"greeting": "ADA"}
    lines = payloads(caplog)
    assert {line["run_id"] for line in lines} == {"demo-run-3"}
    assert {line["function"] for line in lines} == {"demo-durable"}


def test_ingest_completed_line_keeps_its_extras(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from living_kb import inngest_app

    async def fake_ingest(
        runner: StepRunner, data: dict[str, object], ports: object
    ) -> dict[str, int]:
        await runner.run("noop", lambda: 1)
        return {"article_id": 9, "passage_count": 3}

    monkeypatch.setattr(inngest_app, "ingest_article", fake_ingest)

    asyncio.run(
        inngest_app.handle_ingest_article(
            context("complete-run-1", {"canonical_id": "kb:pub:post"})
        )
    )

    completed = [line for line in payloads(caplog) if line["message"] == "ingest-article completed"]
    assert len(completed) == 1
    assert (completed[0]["article_id"], completed[0]["passage_count"]) == (9, 3)


def test_secret_settings_are_redacted_everywhere(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from living_kb.config import settings

    database_url = "postgresql+asyncpg://reader:hunter2secret@db:5432/kb"
    openai_key = "sk-fake-openai-123456"
    event_key = "evt-fake-inngest-123456"
    signing_key = "sign-fake-inngest-654321"
    monkeypatch.setattr(settings, "openai_api_key", openai_key)
    monkeypatch.setattr(settings, "inngest_event_key", event_key)
    monkeypatch.setattr(settings, "inngest_signing_key", signing_key)
    monkeypatch.setattr(settings, "postgres_password", "hunter2secret")
    monkeypatch.setattr(settings, "database_url", database_url)

    try:
        raise ValueError(f"failure mentioning {openai_key}")
    except ValueError:
        logging.getLogger("living_kb.edge").error(
            f"{openai_key} {event_key} {signing_key} {database_url}",
            extra={
                "payload": {"token": signing_key},
                "opaque": Opaque(openai_key),
            },
            exc_info=True,
        )

    rendered = caplog.text
    for secret in (openai_key, event_key, signing_key, "hunter2secret", database_url):
        assert secret not in rendered
    assert "[REDACTED]" in rendered
    assert len(payloads(caplog)) == 1

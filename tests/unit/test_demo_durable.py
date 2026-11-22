import asyncio

import pytest

from living_kb.durable import InjectedFailure, JournalRunner
from living_kb.inngest_app import demo_pipeline, functions


def test_the_demo_durable_function_replaces_the_skeleton_placeholder() -> None:
    local_ids = {fn.local_id for fn in functions}

    assert "demo-durable" in local_ids
    assert "skeleton-ping" not in local_ids


def test_the_demo_pipeline_runs_its_steps_and_resumes_after_a_failure() -> None:
    runner = JournalRunner()
    runner.fail_next("shout")
    with pytest.raises(InjectedFailure):
        asyncio.run(runner.execute(lambda r: demo_pipeline(r, name="living-kb")))

    assert asyncio.run(runner.execute(lambda r: demo_pipeline(r, name="living-kb"))) == {
        "greeting": "HELLO, LIVING-KB"
    }

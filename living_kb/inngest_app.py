"""The Inngest client and the functions registered with it."""

from __future__ import annotations

import logging

import inngest

from living_kb.config import settings
from living_kb.durable import InngestRunner, StepRunner

inngest_client = inngest.Inngest(
    app_id=settings.inngest_app_id,
    api_base_url=settings.inngest_base_url,
    event_api_base_url=settings.inngest_base_url,
    event_key=settings.inngest_event_key or None,
    signing_key=settings.inngest_signing_key or None,
    is_production=not settings.inngest_dev,
    logger=logging.getLogger("uvicorn.inngest"),
)


async def demo_pipeline(runner: StepRunner, *, name: str) -> dict[str, str]:
    """Prove the seam with two named steps and no Inngest dependency."""
    greeting = await runner.run("greet", lambda: f"hello, {name}")
    shouted = await runner.run("shout", lambda: greeting.upper())
    return {"greeting": shouted}


@inngest_client.create_function(
    fn_id="demo-durable",
    trigger=inngest.TriggerEvent(event="kb/demo.requested"),
    retries=settings.step_retries,
)
async def demo_durable(ctx: inngest.Context) -> dict[str, str]:
    """Run the demo pipeline with the real engine's step runner."""
    return await demo_pipeline(InngestRunner(ctx), name=str(ctx.event.data["name"]))


functions = [demo_durable]

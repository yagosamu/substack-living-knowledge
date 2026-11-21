"""The Inngest client and the functions registered with it."""

from __future__ import annotations

import logging

import inngest

from living_kb.config import settings

inngest_client = inngest.Inngest(
    app_id=settings.inngest_app_id,
    api_base_url=settings.inngest_base_url,
    event_api_base_url=settings.inngest_base_url,
    event_key=settings.inngest_event_key or None,
    signing_key=settings.inngest_signing_key or None,
    is_production=not settings.inngest_dev,
    logger=logging.getLogger("uvicorn.inngest"),
)


@inngest_client.create_function(
    fn_id="skeleton-ping",
    trigger=inngest.TriggerEvent(event="kb/skeleton.ping"),
)
async def skeleton_ping(ctx: inngest.Context) -> dict[str, bool]:
    return {"ok": True}


functions = [
    skeleton_ping,  # remove when the first real function lands
]

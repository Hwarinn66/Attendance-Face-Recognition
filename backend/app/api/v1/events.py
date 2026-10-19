"""Server-Sent Events for the web console: attendance changes appear without a page refresh."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.api.deps import ContextDep, SettingsDep, get_auth, require
from app.core.permissions import Permission
from app.db.session import get_session_factory
from app.services.live import broadcaster

router = APIRouter(prefix="/events", tags=["events"])

RECONNECT_MS = 3000


async def _stream(lifetime: float, heartbeat: float) -> AsyncIterator[str]:
    queue = broadcaster.subscribe()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + lifetime
    try:
        # The first bytes flush proxy buffers and tell EventSource how soon to reconnect.
        yield f"retry: {RECONNECT_MS}\n: connected\n\n"
        while (remaining := deadline - loop.time()) > 0:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=min(heartbeat, remaining))
            except TimeoutError:
                yield ": ping\n\n"  # keeps idle proxies from closing the connection
                continue
            yield f"event: attendance\ndata: {payload}\n\n"
    finally:
        broadcaster.unsubscribe(queue)


@router.get("/attendance")
def attendance_events(request: Request, settings: SettingsDep, ctx: ContextDep) -> StreamingResponse:
    """Stream attendance events (check-in/out, corrections, deletions, failed face checks) to administrators.

    Authentication uses its own short-lived DB session instead of the per-request one: a stream stays open
    for minutes and must not hold a pooled connection for that long.
    """
    with get_session_factory()() as db:
        auth = get_auth(request, db, settings, ctx)
        require(Permission.ATTENDANCE_READ_ALL)(auth, request, ctx)
    return StreamingResponse(
        _stream(settings.live_events_stream_seconds, settings.live_events_heartbeat_seconds),
        media_type="text/event-stream",
        headers={
            # no-transform keeps compressing proxies (and the Next.js dev server) from buffering the stream.
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )

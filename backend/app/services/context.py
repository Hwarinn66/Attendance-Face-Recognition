from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class RequestContext:
    ip_address: str | None
    user_agent: str | None
    request_id: str | None

    @classmethod
    def from_request(cls, request: Request, settings: Settings) -> RequestContext:
        ip = request.client.host if request.client else None
        if settings.trust_forwarded_for:
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                # Left-most entry is the original client when every hop is a trusted proxy.
                ip = forwarded.split(",")[0].strip()[:64] or ip
        ua = request.headers.get("user-agent")
        return cls(
            ip_address=ip[:64] if ip else None,
            user_agent=ua[:256] if ua else None,
            request_id=getattr(request.state, "request_id", None),
        )


SYSTEM_CONTEXT = RequestContext(ip_address=None, user_agent="system", request_id=None)

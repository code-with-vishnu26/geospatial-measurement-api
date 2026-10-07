"""Pure-ASGI middleware: request body size limit and request logging."""

import logging
import time
import uuid

from fastapi import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("app.request")


def _too_large_message(limit: int) -> str:
    return f"File exceeds the maximum upload size of {limit // (1024 * 1024)} MB."


class MaxBodySizeMiddleware:
    """Reject oversized request bodies *before* they are buffered.

    Starlette spools a multipart upload to disk before the endpoint runs, so a
    size check inside the endpoint alone would still let a client fill the
    disk. Requests announcing a too-large ``Content-Length`` are refused
    immediately; streamed bodies are counted and cut off at the limit.
    """

    def __init__(self, app: ASGIApp, max_upload_bytes: int, overhead_bytes: int) -> None:
        self.app = app
        self.max_upload_bytes = max_upload_bytes
        self.max_body_bytes = max_upload_bytes + overhead_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        content_length = headers.get(b"content-length")
        declared = content_length.decode() if content_length else ""
        if declared.isdigit() and int(declared) > self.max_body_bytes:
            response = JSONResponse(
                status_code=413,
                content={
                    "error": {
                        "code": "FILE_TOO_LARGE",
                        "message": _too_large_message(self.max_upload_bytes),
                        "details": {},
                    }
                },
            )
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise HTTPException(413, _too_large_message(self.max_upload_bytes))
            return message

        await self.app(scope, limited_receive, send)


class RequestLoggingMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex[:12]
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            logger.info(
                "Request handled",
                extra={
                    "request_id": request_id,
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )

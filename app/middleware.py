from fastapi.responses import JSONResponse

from .config import get_settings


class BodyLimitMiddleware:
    """Bound request bodies before multipart parsing can spool upload content."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] not in {"/api/extract", "/api/export/csv"}:
            await self.app(scope, receive, send)
            return
        settings = get_settings()
        limit = settings.max_upload_mb * 1024 * 1024 + 65536
        if scope["path"] == "/api/export/csv":
            limit = 1024 * 1024
        chunks = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > limit:
                response = JSONResponse(
                    {
                        "error": {
                            "code": "request_size",
                            "message": "Request exceeds the configured upload limit.",
                        }
                    },
                    status_code=413,
                )
                await response(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        body = b"".join(chunks)
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

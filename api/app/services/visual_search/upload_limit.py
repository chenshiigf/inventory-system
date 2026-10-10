"""Bound visual request bodies before multipart spooling, including chunked uploads."""
from starlette.responses import JSONResponse


class BodyTooLarge(Exception):
    pass


class VisualUploadLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/visual-search"):
            return await self.app(scope, receive, send)
        limit = 11 * 1024 * 1024
        headers = dict(scope["headers"])
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = limit + 1
        response = JSONResponse({"detail": "图片不能超过 10MB"}, status_code=413)
        if declared > limit:
            return await response(scope, receive, send)
        received = 0
        exceeded = False
        response_started = False

        async def bounded_receive():
            nonlocal received, exceeded
            message = await receive()
            received += len(message.get("body", b""))
            if received > limit:
                exceeded = True
                raise BodyTooLarge()
            return message

        async def bounded_send(message):
            nonlocal response_started
            if exceeded:
                return  # Suppress multipart's generic 400 to deliver a clear 413.
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, bounded_receive, bounded_send)
        except BodyTooLarge:
            if not exceeded:
                raise
        if exceeded and not response_started:
            await response(scope, receive, send)

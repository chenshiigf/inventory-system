"""Reject overlapping HTTP image searches before FastAPI parses multipart bodies."""
import threading
from pathlib import Path

from starlette.responses import JSONResponse

from .lock import CallLock


class VisualSearchAdmission:
    """A non-waiting cross-worker gate for the expensive search request path.

    The in-process lock avoids same-worker contention and the file lock shares
    admission across workers. This outer ASGI gate prevents another worker from
    consuming or spooling a search body while one search request is active. The
    service's separate OS lock remains the guard shared with direct CLI calls and
    indexing, and is acquired before image decoding.
    """

    def __init__(self, app, lock_root: Path):
        self.app = app
        self._lock = threading.Lock()
        self._process_lock = CallLock(Path(lock_root), filename="http-search-admission.lock")

    async def __call__(self, scope, receive, send):
        if not (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and scope["path"] == "/api/visual-search/search"
        ):
            return await self.app(scope, receive, send)

        if not self._lock.acquire(blocking=False):
            return await self._busy(scope, receive, send)
        try:
            process_acquired = self._process_lock.acquire()
        except OSError:
            self._lock.release()
            response = JSONResponse({"detail": "找货服务暂不可用，请稍后重试"}, status_code=503)
            return await response(scope, receive, send)
        if not process_acquired:
            self._lock.release()
            return await self._busy(scope, receive, send)

        try:
            return await self.app(scope, receive, send)
        finally:
            try:
                self._process_lock.release()
            finally:
                self._lock.release()

    @staticmethod
    async def _busy(scope, receive, send):
        response = JSONResponse(
            {"detail": "AI找货正忙，请稍后重试"},
            status_code=429,
            headers={"Retry-After": "3"},
        )
        return await response(scope, receive, send)

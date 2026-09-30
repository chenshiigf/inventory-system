"""One cooperative preview worker per application process, with bounded retention."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, Lock

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from .cancellation import PreviewCancelled
from .excel_reader import ProductImportWorkbookError, read_import_workbook
from .schemas import ProductImportPreviewTaskResponse
from .session_store import SOURCE_FILE_NAME, PreviewSessionLifecycle, create_preview_session, delete_preview_session

logger = logging.getLogger(__name__)
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
BUSY_MESSAGE = "当前已有批量导入任务正在处理，请等待完成或先取消当前任务。"


class PreviewBusy(Exception):
    pass


@dataclass
class PreviewTask:
    task_id: str
    directory: Path
    response: ProductImportPreviewTaskResponse
    cancel: Event = field(default_factory=Event)
    finished_at: float | None = None

    def checkpoint(self) -> None:
        if self.cancel.is_set():
            raise PreviewCancelled()


class PreviewTaskRegistry:
    def __init__(self, sessions: PreviewSessionLifecycle, ttl_seconds: float = 3600) -> None:
        self.sessions = sessions
        self.ttl_seconds = ttl_seconds
        self._guard = Lock()
        self._heavy = Lock()
        self._tasks: dict[str, PreviewTask] = {}
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="import-preview")

    def acquire(self) -> None:
        if not self._heavy.acquire(blocking=False):
            raise PreviewBusy(BUSY_MESSAGE)

    def release(self) -> None:
        self._heavy.release()

    def cleanup(self) -> None:
        cutoff = time.monotonic() - self.ttl_seconds
        with self._guard:
            for task_id, task in list(self._tasks.items()):
                if task.finished_at is not None and task.finished_at <= cutoff:
                    del self._tasks[task_id]
        # Successful preview files retain the existing commit/session lifecycle.

    def get(self, task_id: str) -> ProductImportPreviewTaskResponse | None:
        self.cleanup()
        with self._guard:
            task = self._tasks.get(task_id)
            return task.response.model_copy(deep=True) if task else None

    def _discard(self, task: PreviewTask) -> str | None:
        try:
            delete_preview_session(task.directory, strict=True)
            return None
        except OSError:
            logger.exception("Unable to clean preview task directory: %s", task.task_id)
            return "预览任务已停止，但临时文件清理失败，请稍后重试取消。"

    def cancel(self, task_id: str) -> ProductImportPreviewTaskResponse | None:
        with self._guard:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            if task.response.status != "failed" and task.response.status != "cancelled":
                task.cancel.set()
            if task.response.status in {"succeeded", "failed"}:
                # Also handles completion racing with a cancellation click.
                cleanup_error = self._discard(task)
                task.response = ProductImportPreviewTaskResponse(
                    task_id=task_id, status="failed" if cleanup_error else "cancelled", error=cleanup_error,
                )
                task.finished_at = time.monotonic()
            return task.response.model_copy(deep=True)

    def start(self, *, task_id: str, directory: Path, engine: Engine,
              file_name: str, already_imported: bool) -> ProductImportPreviewTaskResponse:
        # Caller owns the heavy reservation and has already saved/prechecked the file.
        task = PreviewTask(task_id, directory, ProductImportPreviewTaskResponse(task_id=task_id, status="queued"))
        self.sessions.reserve(directory)
        with self._guard:
            self._tasks[task_id] = task
        try:
            self._executor.submit(self._run, task, engine, file_name, already_imported)
        except Exception:
            with self._guard:
                del self._tasks[task_id]
            self.sessions.release(directory)
            raise
        return self.get(task_id) or task.response

    def _run(self, task: PreviewTask, engine: Engine, file_name: str, already_imported: bool) -> None:
        response = ProductImportPreviewTaskResponse(task_id=task.task_id, status="processing", phase="images")
        with self._guard:
            task.response = response
        try:
            task.checkpoint()
            data = (task.directory / SOURCE_FILE_NAME).read_bytes()
            rows = read_import_workbook(data, checkpoint=task.checkpoint)
            task.checkpoint()
            with self._guard:
                task.response.phase = "building_preview"
            with Session(engine) as db:
                result = create_preview_session(
                    data=data, rows=rows, db=db, preview_directory=task.directory.parent,
                    file_name=file_name, already_imported=already_imported,
                    session_id=task.task_id, checkpoint=task.checkpoint,
                )
            # Publish success and check cancellation under the same guard as cancel().
            with self._guard:
                task.checkpoint()
                response = ProductImportPreviewTaskResponse(task_id=task.task_id, status="succeeded", result=result)
        except PreviewCancelled:
            cleanup_error = self._discard(task)
            response = ProductImportPreviewTaskResponse(
                task_id=task.task_id, status="failed" if cleanup_error else "cancelled", error=cleanup_error,
            )
        except Exception as error:
            cleanup_error = self._discard(task)
            logger.exception("Product import preview task failed: %s", task.task_id)
            response = ProductImportPreviewTaskResponse(
                task_id=task.task_id, status="failed",
                error=cleanup_error or (str(error) if isinstance(error, ProductImportWorkbookError) else "商品预览处理失败，请重新上传 Excel。"),
            )
        finally:
            with self._guard:
                if response.status == "succeeded" and task.cancel.is_set():
                    cleanup_error = self._discard(task)
                    response = ProductImportPreviewTaskResponse(
                        task_id=task.task_id, status="failed" if cleanup_error else "cancelled", error=cleanup_error,
                    )
                # Terminal status means the reservation has also been released.
                self.release()
                task.response = response
                task.finished_at = time.monotonic()
            self.sessions.release(task.directory)

    def close(self) -> None:
        with self._guard:
            for task in self._tasks.values():
                if task.response.status not in TERMINAL_STATUSES:
                    task.cancel.set()
        self._executor.shutdown(wait=True)
        self.cleanup()

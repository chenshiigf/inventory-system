"""Opt-in, single-process drain gate. No HTTP control routes or stop commands."""
import asyncio
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, closing, contextmanager
from contextvars import ContextVar
import json
import math
import os
from pathlib import Path
import socket
import sqlite3
import stat
import struct
import sys
import threading
import time

from starlette.responses import JSONResponse


def checked_path(path, *, existing=False):
    raw = Path(path).expanduser()
    if not raw.is_absolute() or '..' in raw.parts:
        raise ValueError('absolute path without .. required')
    for part in [*reversed(raw.parents), raw]:
        if not os.path.lexists(part):
            continue
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('linked path rejected')
        if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
            raise ValueError('hard-linked file rejected')
    if existing and not raw.is_file():
        raise ValueError('required regular file missing')
    return raw.resolve()


def connect(path):
    path = checked_path(path, existing=True)
    for suffix in ('-wal', '-shm', '-journal'):
        if os.path.lexists(str(path) + suffix):
            raise ValueError('SQLite sidecar present; no recovery attempted')
    with path.open('rb') as stream:
        header = stream.read(20)
    if header[:16] == b'SQLite format 3\0' and 2 in header[18:20]:
        raise ValueError('WAL database requires separate review')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA query_only=ON')
    return db


@contextmanager
def existing_lock(path):
    # Preserve the existing byte-range lock on Windows and flock elsewhere.
    with checked_path(path, existing=True).open('r+b') as stream:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)

_request = ContextVar('maintenance_request', default=None)


class MaintenanceRejected(RuntimeError):
    pass


class DrainGate:
    def __init__(self, visual_root, catalog_root):
        self.visual_root, self.catalog_root = Path(visual_root), Path(catalog_root)
        self._lock = threading.RLock()
        self.active = False
        self.deadline = 0.0
        self.generation = 0
        self.fault = None
        self.http = self.pending = self.queued = self.running = 0
        self.activities = Counter()
        self.observers = []
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='maintenance-audit')
        self._probe = None
        self._leases = None
        self._commands = asyncio.Lock()
        self._server = None
        self._socket_path = None
        self._socket_identity = None

    def fail(self, reason):
        with self._lock:
            self.fault = self.fault or reason

    def _admitted(self):
        request = _request.get()
        return request is not None and request['gate'] is self and request['live']

    def check_entry(self):
        with self._lock:
            if self.active and not self._admitted():
                raise MaintenanceRejected('maintenance admission closed')

    @contextmanager
    def activity(self, kind):
        with self._lock:
            self.check_entry()
            self.activities[kind] += 1
        try:
            yield
        finally:
            with self._lock:
                self.activities[kind] -= 1

    def submit(self, executor, function, *args):
        with self._lock:
            self.check_entry()
            self.pending += 1
            self.queued += 1
        state = {'queued': True}
        def run():
            with self._lock:
                state['queued'] = False
                self.queued -= 1
                self.running += 1
            try:
                return function(*args)
            finally:
                with self._lock:
                    self.running -= 1
        def done(future):
            with self._lock:
                if state['queued']:
                    self.queued -= 1
                self.pending -= 1
                if not future.cancelled() and future.exception() is not None:
                    self.fail('background_failure')
        try:
            future = executor.submit(run)
        except BaseException:
            with self._lock:
                self.pending -= 1
                self.queued -= 1
            self.fail('submission_failed')
            raise
        future.add_done_callback(done)
        return future

    def counts(self):
        with self._lock:
            return {'http': self.http, 'futures': self.pending, 'queued': self.queued,
                    'running': self.running, 'activities': sum(self.activities.values())}

    def _audit(self):
        if not self.observers:
            raise ValueError('missing task observer')
        if self._leases is None:
            stack = ExitStack()
            try:
                for path in (self.visual_root / 'calls.lock', self.catalog_root / 'calls.lock'):
                    if sys.platform == 'linux':
                        import fcntl
                        stream = stack.enter_context(checked_path(path, existing=True).open('rb'))
                        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    else:
                        stack.enter_context(existing_lock(path))
            except BaseException:
                stack.close()
                raise
            self._leases = stack
        metrics = {}
        for observer in self.observers:
            for key, value in observer().items():
                if type(value) is not int or value < 0:
                    raise ValueError('unknown observer state')
                metrics[key] = metrics.get(key, 0) + value
        # Never initialize, migrate, recover, or write the ledger/index here.
        with closing(connect(self.visual_root / 'daily-budget.sqlite3')) as db:
            if db.execute('PRAGMA user_version').fetchone()[0] != 1:
                raise ValueError('unknown budget schema')
            metrics['reserved_calls'] = db.execute("SELECT count(*) FROM call_reservations WHERE state='reserved'").fetchone()[0]
            metrics['unresolved_calls'] = db.execute("SELECT count(*) FROM call_reservations WHERE state IS NULL OR state NOT IN ('reserved','settled','unknown')").fetchone()[0]
        indices = [self.visual_root / 'index.db', *sorted((self.visual_root / 'production-indices').glob('*/index.db'))]
        metrics['index_jobs'] = 0
        for path in indices:
            checked_path(path)
            if path.exists():
                with closing(connect(path)) as db:
                    metrics['index_jobs'] += db.execute("SELECT count(*) FROM visual_jobs WHERE status IS NULL OR status NOT IN ('completed','failed','interrupted')").fetchone()[0]
        return metrics

    def _release(self):
        if self._leases is not None:
            self._leases.close()
            self._leases = None

    def _blocked(self, reason, **values):
        return {'state': 'BLOCKED', 'reason': reason, 'pid': os.getpid(),
                'generation': self.generation, 'maintenance': self.active,
                'deadline': self.deadline, 'counts': self.counts(), **values}

    async def status(self):
        if not self.active:
            return self._blocked('maintenance_not_enabled')
        if time.monotonic() >= self.deadline:
            return self._blocked('drain_deadline_expired')
        if self.fault:
            return self._blocked(self.fault)
        if any(self.counts().values()):
            return self._blocked('work_in_flight')
        if self._probe is not None and not self._probe.done():
            return self._blocked('audit_in_progress')
        generation = self.generation
        self._probe = self._executor.submit(self._audit)
        try:
            metrics = await asyncio.wait_for(asyncio.shield(asyncio.wrap_future(self._probe)), 1)
        except Exception as error:
            self.fail('audit_' + type(error).__name__)
            return self._blocked(self.fault)
        with self._lock:
            if (not self.active or self.generation != generation or self.fault or
                    time.monotonic() >= self.deadline or any(self.counts().values())):
                return self._blocked('state_changed')
            if any(metrics.values()):
                return self._blocked('not_drained', checks=metrics)
            return {'state': 'SAFE_TO_STOP', 'pid': os.getpid(), 'generation': self.generation,
                    'maintenance': True, 'deadline': self.deadline,
                    'counts': self.counts(), 'checks': metrics}

    async def command(self, request):
        async with self._commands:
            operation = request.get('command')
            if operation == 'begin':
                seconds = request.get('timeout_seconds', 90)
                if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 90:
                    return self._blocked('invalid_deadline')
                with self._lock:
                    if not self.active:
                        self.active = True
                        self.generation += 1
                        self.deadline = time.monotonic() + seconds
            elif operation == 'resume':
                if self._probe is not None and not self._probe.done():
                    return self._blocked('audit_in_progress')
                await asyncio.get_running_loop().run_in_executor(self._executor, self._release)
                with self._lock:
                    self.active = False
                    self.generation += 1
                return {'state': 'RUNNING', 'pid': os.getpid(), 'maintenance': False}
            elif operation != 'status':
                return self._blocked('invalid_command')
            return await self.status()

    async def _client(self, reader, writer):
        reply = {'state': 'BLOCKED', 'reason': 'permission_denied'}
        try:
            peer = writer.get_extra_info('socket').getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i'))
            if struct.unpack('3i', peer)[1] == 0:
                line = await asyncio.wait_for(reader.readline(), 2)
                if len(line) > 4096:
                    raise ValueError('oversized command')
                request = json.loads(line)
                reply = await self.command(request)
                reply['nonce'] = request.get('nonce')
        except Exception:
            reply = self._blocked('control_error')
        finally:
            writer.write((json.dumps(reply) + '\n').encode())
            try:
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

    async def start(self, path):
        if not hasattr(socket, 'SO_PEERCRED'):
            raise RuntimeError('maintenance control requires Linux peer credentials')
        path = checked_path(path)
        info = path.parent.stat()
        if info.st_uid not in {0, os.geteuid()} or stat.S_IMODE(info.st_mode) & 0o022:
            raise ValueError('maintenance socket parent is not private')
        sock = socket.socket(socket.AF_UNIX)
        try:
            sock.bind(str(path))  # refuses any pre-existing path; no unlink/replacement
            path.chmod(0o600)
            self._socket_path = path
            self._socket_identity = (path.stat().st_dev, path.stat().st_ino)
            sock.listen(4)
            sock.setblocking(False)
            self._server = await asyncio.start_unix_server(self._client, sock=sock, limit=4096)
        except BaseException:
            sock.close()
            raise

    async def close(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        await asyncio.to_thread(self._executor.shutdown, wait=True)
        self._release()
        if self._socket_path and self._socket_path.exists():
            info = self._socket_path.lstat()
            if stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == self._socket_identity:
                self._socket_path.unlink()  # only our control socket, never a calls.lock


class MaintenanceMiddleware:
    def __init__(self, app, gate):
        self.app, self.gate = app, gate

    async def __call__(self, scope, receive, send):
        if scope['type'] not in {'http', 'websocket'}:
            return await self.app(scope, receive, send)
        gate = self.gate
        with gate._lock:
            gate.http += 1
            admitted = not gate.active
            request = {'gate': gate, 'live': admitted}
        token = _request.set(request)
        try:
            if not admitted:
                if scope['type'] == 'websocket':
                    await send({'type': 'websocket.close', 'code': 1001})
                else:
                    await JSONResponse({'detail': '系统维护中，请稍后重试'}, status_code=503,
                                       headers={'Connection': 'close', 'Retry-After': '30'})(scope, receive, send)
            else:
                await self.app(scope, receive, send)
        except BaseException:
            if admitted:
                # An aborted ASGI call may leave a synchronous dependency running.
                # Never infer that unknown work is finished from a decremented HTTP count.
                gate.fail('http_aborted_or_failed')
            raise
        finally:
            with gate._lock:
                request['live'] = False
                gate.http -= 1
            _request.reset(token)

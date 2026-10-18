"""Persistent, operation-bound write admission. No application/AI imports."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import stat
import sqlite3
import time
from uuid import uuid4

class MaintenanceError(ValueError):
    pass


def checked_path(value, *, existing=False):
    path = Path(value).absolute()
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise MaintenanceError("拒绝链接、junction 或 reparse 路径")
    path = path.resolve()
    if path.exists() and path.is_file() and path.stat().st_nlink != 1:
        raise MaintenanceError("拒绝硬链接")
    if existing and (not path.is_file() or not stat.S_ISREG(path.stat().st_mode)):
        raise MaintenanceError("要求已存在的普通文件")
    return path


def database_identity(path):
    path = checked_path(path, existing=True)
    info = path.stat()
    return {"path": str(path), "device": info.st_dev, "inode": info.st_ino}


def atomic_json(path, data, *, group=None):
    path = checked_path(path)
    temporary = path.with_name('.' + path.name + '-' + uuid4().hex)
    try:
        with temporary.open('x', encoding='utf8') as stream:
            json.dump(data, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != 'nt':
            temporary.chmod(0o640)
            if group is not None and os.geteuid() == 0:
                os.chown(temporary, -1, group)
        os.replace(temporary, path)
        if os.name != 'nt':
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        if temporary.exists():
            temporary.unlink()


@contextmanager
def file_gate(path, *, exclusive=True):
    """Each request owns a handle; shared requests, exclusive maintenance."""
    path = checked_path(path)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    stream = os.fdopen(os.open(path, flags, 0o660), 'r+b')
    acquired = False
    try:
        if os.fstat(stream.fileno()).st_nlink != 1:
            raise MaintenanceError('写入门禁是硬链接')
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            import msvcrt
            class Overlapped(ctypes.Structure):
                _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                            ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]
            kernel = ctypes.WinDLL('kernel32', use_last_error=True)
            kernel.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                          wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
            kernel.LockFileEx.restype = wintypes.BOOL
            kernel.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                            wintypes.DWORD, ctypes.POINTER(Overlapped)]
            kernel.UnlockFileEx.restype = wintypes.BOOL
            overlapped = Overlapped()
            handle = msvcrt.get_osfhandle(stream.fileno())
            # FAIL_IMMEDIATELY=1, EXCLUSIVE_LOCK=2. Shared admission must not
            # change normal product concurrency or the AI service's own 429.
            if not kernel.LockFileEx(handle, 1 | (2 if exclusive else 0), 0, 1, 0, ctypes.byref(overlapped)):
                raise MaintenanceError('业务写入尚未排空或操作锁被占用')
        else:
            import fcntl
            try:
                fcntl.flock(stream, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except OSError as error:
                raise MaintenanceError('业务写入尚未排空或操作锁被占用') from error
        acquired = True
        yield
    finally:
        if acquired:
            if os.name == 'nt':
                if not kernel.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlapped)):
                    raise OSError(ctypes.get_last_error(), 'Cannot unlock business write gate')
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_UN)
        stream.close()


class Maintenance:
    def __init__(self, data_root):
        self.root = Path(data_root).absolute()
        self.directory = self.root / 'numbering-maintenance'
        self.path = self.directory / 'state.json'
        self.gate = self.root / 'business-writes.lock'

    def read(self):
        checked_path(self.root)
        checked_path(self.directory)
        if not self.directory.exists():
            return None
        try:
            state = json.loads(checked_path(self.path, existing=True).read_text(encoding='utf8'))
            if (state.get('format') != 'numbering-maintenance-v1' or
                    state.get('state') not in ('held', 'released') or
                    not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{7,95}', state.get('operation_id', '')) or
                    not re.fullmatch(r'[0-9a-f]{40}', state.get('release_sha', '')) or
                    not isinstance(state.get('database_identity'), dict)):
                raise ValueError('invalid maintenance state')
            return state
        except (OSError, ValueError, TypeError, AttributeError) as error:
            raise MaintenanceError('维护状态缺失或损坏，保持禁止写入') from error

    def held(self):
        state = self.read()
        return state is not None and state['state'] == 'held'

    def require(self, operation_id, database):
        state = self.read()
        if (not state or state['state'] != 'held' or state['operation_id'] != operation_id or
                state['database_identity'] != database_identity(database)):
            raise MaintenanceError('维护状态、操作 ID 或数据库身份不匹配')
        return state

    def enter(self, operation_id, release_sha, database, frozen_sha256, *, frozen_counters=None):
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{7,95}', operation_id):
            raise MaintenanceError('操作 ID 必须为 8 至 96 位字母、数字、下划线或横线')
        if not re.fullmatch(r'[0-9a-f]{40}', release_sha):
            raise MaintenanceError('发布 SHA 无效')
        identity = database_identity(database)
        with file_gate(self.gate):
            existing = self.read()
            if existing:
                receipt = existing.get('abort_receipt')
                if not (existing.get('state') == 'released' and
                        existing.get('release_reason') == 'precommit_abort' and
                        isinstance(receipt, dict) and receipt.get('format') == 'numbering-abort-receipt-v1' and
                        receipt.get('state') == 'complete' and
                        receipt.get('operation_id') == existing.get('operation_id')):
                    raise MaintenanceError('已存在维护操作，必须检查并恢复原操作，不自动替换')
                history = self.directory / 'history'
                checked_path(history)
                history.mkdir(mode=0o750, exist_ok=True)
                archive = history / (existing['operation_id'] + '.json')
                checked_path(archive)
                if archive.exists() or archive.is_symlink():
                    raise MaintenanceError('已中止操作的归档已存在；拒绝覆盖审计记录')
                atomic_json(archive, existing, group=Path(database).stat().st_gid)
            else:
                self.directory.mkdir(mode=0o750, exist_ok=True)
            group = Path(database).stat().st_gid
            if os.name != 'nt' and os.geteuid() == 0:
                os.chown(self.directory, -1, group)
                os.chown(self.gate, -1, group)
                self.gate.chmod(0o660)
            state = {'format': 'numbering-maintenance-v1', 'operation_id': operation_id,
                     'release_sha': release_sha, 'database_identity': identity, 'state': 'held',
                     'entered_at_ns': time.time_ns(), 'frozen_business_sha256': frozen_sha256,
                     'frozen_counters': frozen_counters or {}}
            atomic_json(self.path, state, group=group)
            return state

    def abort_release(self, operation_id, database, receipt):
        """Release only a verified pre-commit deployment abort, never a cutover."""
        state = self.require(operation_id, database)
        path = checked_path(database, existing=True)
        connection = None
        try:
            connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)
            if connection.execute('PRAGMA integrity_check').fetchall() != [('ok',)] or connection.execute('PRAGMA foreign_key_check').fetchall():
                raise MaintenanceError('数据库完整性检查未通过；不能解除中止维护')
            registered = connection.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='production_numbering_cutovers'").fetchone()[0]
            if registered and connection.execute('SELECT count(*) FROM production_numbering_cutovers').fetchone()[0]:
                raise MaintenanceError('历史换号已提交；普通中止流程禁止解除维护')
        except sqlite3.Error as error:
            raise MaintenanceError('无法确认历史换号未提交；不能解除中止维护') from error
        finally:
            if connection is not None:
                connection.close()
        expected = {
            'format': 'numbering-abort-receipt-v1',
            'state': 'complete',
            'operation_id': operation_id,
            'release_sha': state['release_sha'],
            'database_identity': state['database_identity'],
            'checks': {
                'cutover_not_committed': True,
                'database_restored': True,
                'old_code_restored': True,
                'api_readonly_probe': True,
                'inventory_readonly_checks': True,
            },
        }
        if any(receipt.get(key) != value for key, value in expected.items()):
            raise MaintenanceError('中止验收记录不完整或与当前维护操作不匹配')
        if not isinstance(receipt.get('plan_sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', receipt['plan_sha256']):
            raise MaintenanceError('中止验收摘要无效')
        if not isinstance(receipt.get('previous_commit'), str) or not re.fullmatch(r'[0-9a-f]{40}', receipt['previous_commit']):
            raise MaintenanceError('旧版本提交摘要无效')
        validation_path = checked_path(self.directory / 'abort-validation.json', existing=True)
        try:
            validation = json.loads(validation_path.read_text(encoding='utf8'))
        except (OSError, ValueError) as error:
            raise MaintenanceError('缺少中止恢复只读验收登记；不能解除维护') from error
        if (validation.get('format') != 'numbering-abort-validation-v1' or
                validation.get('state') != 'probe_passed' or
                validation.get('operation_id') != operation_id or
                validation.get('release_sha') != state['release_sha'] or
                validation.get('database_identity') != state['database_identity'] or
                validation.get('plan_sha256') != receipt['plan_sha256'] or
                validation.get('previous_commit') != receipt['previous_commit'] or
                validation.get('checks') != expected['checks']):
            raise MaintenanceError('中止只读验收登记与解除请求不匹配')
        atomic_json(self.path, {**state, 'state': 'released', 'release_reason': 'precommit_abort',
                               'abort_receipt': receipt, 'released_at_ns': time.time_ns()},
                    group=Path(database).stat().st_gid)

    def rehold_after_abort_failure(self, operation_id, database, reason):
        """Close admission again if post-restore startup fails before proxy resume."""
        state = self.read()
        if (not state or state.get('state') != 'released' or
                state.get('release_reason') != 'precommit_abort' or
                state.get('operation_id') != operation_id or
                state.get('database_identity') != database_identity(database)):
            raise MaintenanceError('不能将非本次中止释放状态重新冻结')
        atomic_json(self.path, {**state, 'state': 'held', 'abort_resume_failure': str(reason)[:500],
                               'reheld_at_ns': time.time_ns()}, group=Path(database).stat().st_gid)

    def release(self, operation_id, database, receipt, completion_sha256):
        """Caller holds the exclusive gate and has verified committed DB state."""
        state = self.require(operation_id, database)
        if (receipt.get('operation_id') != operation_id or receipt.get('release_sha') != state['release_sha'] or
                receipt.get('database_identity') != state['database_identity'] or
                receipt.get('completion_sha256') != completion_sha256 or
                receipt.get('checks') != {'inventory': True, 'ai_metadata': True, 'old_sessions': True, 'web': True}):
            raise MaintenanceError('缺少匹配的完整验收记录；换号成功不等于可以解除维护')
        atomic_json(self.path, {**state, 'state': 'released', 'acceptance': receipt,
                               'released_at_ns': time.time_ns()}, group=Path(database).stat().st_gid)


class MaintenanceAdmission:
    def __init__(self, app, data_root):
        self.app = app
        self.maintenance = Maintenance(data_root)

    async def __call__(self, scope, receive, send):
        guarded = (scope['type'] == 'http' and scope['path'].startswith('/api/') and
                   (scope['method'] not in {'GET', 'HEAD', 'OPTIONS'} or
                    scope['path'].startswith('/api/visual-search/')))
        if not guarded:
            return await self.app(scope, receive, send)
        # Keep the shared admission lock until the entire request has completed.
        # Entering maintenance cannot race an admitted multipart/image writer.
        gate = file_gate(self.maintenance.gate, exclusive=False)
        try:
            gate.__enter__()
            try:
                if self.maintenance.held():
                    raise MaintenanceError('编号切换维护中，暂时禁止业务写入')
            except BaseException:
                gate.__exit__(*__import__('sys').exc_info())
                raise
        except (MaintenanceError, OSError):
            from starlette.responses import JSONResponse
            response = JSONResponse({'detail': '系统维护中或写入门禁不可用，请稍后重试'},
                                    status_code=503, headers={'Retry-After': '30'})
            return await response(scope, receive, send)
        try:
            return await self.app(scope, receive, send)
        finally:
            gate.__exit__(None, None, None)
